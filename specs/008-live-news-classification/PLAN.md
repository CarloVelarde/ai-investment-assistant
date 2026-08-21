# Implementation Plan: Live News and Classification

**Document status:** Approved

## Approach

Build one thin, restart-safe path through the existing local process:

1. Expand the internal news article and classification models and add a SQLite
   migration for articles, per-ticker classifications, classifier budgets, and a
   durable news retrieval high-water mark.
2. Add an Alpaca news port and REST adapter that follows page tokens and converts
   every provider payload before core logic sees it.
3. Apply deterministic watchlist, recency, required-field/source, identity,
   canonical-URL, and budget filters before a classifier call.
4. Add a narrow classifier port with a fake and an OpenAI Responses adapter using
   strict structured output. Validate every result again as an internal model.
5. Convert qualifying classifications into the existing `NewsSignal` and submit
   them to the existing `EventManager` without changing event policy.
6. Run one bounded news poll at startup and on the existing live-loop cadence,
   independent of market-session and stock-socket state.

This is a single vertical slice. It adds no research implementation, notification
adapter, scheduler, worker, second socket, or alternate provider.

## Existing implementation seams

- `models.NewsRecord` is the minimal offline article shape and should evolve into
  or be replaced by a provenance-complete live article model.
- `models.NewsSignal` and SQLite signal persistence already support directionless
  news, category correlation, stable signal identity, and source provenance.
- `detection.detect_news_signal` is the temporary negative-phrase demo. Keep it
  for offline fixture compatibility until tests migrate, but do not route live
  news through that product-incomplete rule.
- `EventManager.handle_signal` already supports news-only events, directional
  market correlation, category correlation, exact deduplication, and material
  requeue for significant new news. Do not add another promotion owner.
- `AlpacaMarketData` demonstrates stdlib HTTP pagination, normalization, safe
  provider errors, and injected clocks/sleepers. News should use a separate
  narrow port even if the production adapter shares credentials and HTTP
  conventions.
- `main.run_live_session` already has startup recovery, open/closed cycles,
  pending processing, heartbeat, and an injected sleeper. Add a bounded news
  step rather than a scheduler abstraction.
- `SQLiteStorage.initialize` owns schema migrations and is the one persistence
  boundary for durable replay state.

## Approved decisions

- Use Alpaca REST news polling rather than the news websocket for this MVP slice.
  This keeps one readable loop and makes startup recovery and pagination the same
  path used during ordinary operation. A news websocket remains a possible later
  optimization if observed polling performance is inadequate (D-029).
- Query `parse_watchlist(settings.watchlist)`, not the market list with
  comparison-only `SPY` appended.
- Use provider article ID as primary identity and canonical URL as exact
  cross-ID duplicate identity. Do not add fuzzy or embedding-based deduplication.
- Persist articles before classification and classifications before signal
  promotion. Rejected and failed attempts remain explainable without creating an
  event.
- Classify per article/ticker pair because relevance and direction may differ
  across companies mentioned in the same story.
- Use fixed, application-enforced retrieval and call budgets. The model cannot
  request retries, tools, more content, or more articles.
- Use the pinned `gpt-5.4-nano-2026-03-17` snapshot through the Responses API,
  `store=false`, no tools, and strict JSON Schema output. The model and prompt
  version are persisted.
- A qualifying result requires relevance, significance, and the approved
  confidence floor. Direction never gates significance.
- Map `UNCLEAR` to `NewsSignal.direction=None`; keep the existing event manager's
  category correlation behavior.
- Keep same-provider-ID revisions saved but quiet for the first slice unless the
  approved spec explicitly permits a new version.

## Proposed internal boundaries

```text
NewsProvider
  fetch_news(symbols, start, end, page_token) -> NewsPage

NewsClassifier
  classify(article, ticker) -> NewsClassification

news ingestion service/functions
  normalize provider payload
  → persist article
  → deterministic eligibility + duplicate/budget checks
  → classify through injected port
  → validate and persist classification
  → convert qualifying result to NewsSignal
  → EventManager.handle_signal
```

Interfaces belong only at the two real external boundaries: Alpaca news and the
classifier. Filtering, budget accounting, signal construction, and promotion
remain direct deterministic application functions.

## Data and migration plan

Add the smallest schema that makes replay and failures explainable:

- `news_articles`: provider/article identity, headline, summary, bounded content,
  canonical URL, source, symbols, created/updated/retrieved times, and content
  fingerprint.
- `news_classifications`: article ID, ticker, prompt version, model version,
  relevance, category, significance, direction, importance, confidence,
  rationale, status, attempt time, and safe error.
- A uniqueness key on article/ticker/prompt/model version prevents duplicate
  successful classification.
- A durable UTC-day call counter or classification-attempt query supplies the
  daily budget without a separate cache/service.
- A small metadata/high-water record supplies incremental retrieval. Requests
  overlap the stored timestamp and rely on stable article identity.

Bump the SQLite version once and migrate existing databases without rewriting
signals or event history. Existing offline `NewsSignal` rows remain readable.

## Data flow

```text
startup or due live-loop cycle
  → load explicit user watchlist + durable news high-water mark
  → fetch bounded Alpaca REST pages oldest-first
  → normalize and validate each article
  → save article + provenance
  → derive watched article/ticker candidates
  → deterministic recency / source / duplicate / budget filters
  → eligible candidate only:
      bounded untrusted article text
      → OpenAI strict structured classification
      → validate + persist result/model/prompt provenance
      → relevant + significant + confidence floor?
          no  → stop; no signal/event/cooldown
          yes → stable NewsSignal
              → existing EventManager
              → existing pending fake research/notify stages
  → save high-water mark after durable article acceptance
```

Market ingest and news ingest are independent sibling paths. Neither one gates or
classifies the other.

## Failure behavior

- Alpaca authentication, permission, rate-limit, timeout, malformed payload, and
  pagination-cap failures remain safe and visible; accepted earlier pages stay
  durable and the next pass can overlap/retry.
- One invalid article does not stop sibling articles or market processing.
- Classifier timeout, refusal, transport error, malformed structured output, or
  validation failure is persisted against the article/ticker candidate with a
  safe retry status. It creates no signal.
- A retry reuses the same article, ticker, prompt, and model identity and must not
  bypass per-pass or per-day budgets.
- Exhausted budget defers otherwise eligible candidates without treating them as
  insignificant.
- No error text may include API keys, raw headers, full provider content, or raw
  model responses.

## Validation

- Normalize representative Alpaca pages, multiple symbols, pagination, missing
  fields, content HTML, duplicate IDs, repeated URLs, and safe provider failures.
- Prove explicit-watchlist behavior: comparison-only `SPY` does not create calls;
  explicitly configured `SPY` can.
- Prove deterministic filters and budgets make zero classifier calls for rejected
  candidates and defer rather than reject over-budget candidates.
- Contract-test fake classifier results for significant earnings beat,
  acquisition, expansion, earnings miss, investigation, recall, and unclear but
  significant management/legal news.
- Reject malformed enums, confidence outside `[0, 1]`, refusal, missing fields,
  oversized rationale, and transport failure before signal creation.
- Prove insignificant and irrelevant results persist with no signal/event.
- Prove positive, negative, and unclear significant classifications can each
  create one news-only event.
- Prove related news enriches/requeues an existing event once through the
  unchanged event manager.
- Prove startup recovery, overlapping pages, same-article restart, retry, and
  canonical-URL duplicates do not duplicate classifier success, signals,
  research, or notification.
- Drive open-session, after-close, and stock-socket-failure cycles with fakes and
  prove news remains independent while pending work continues.
- Add an opt-in manual smoke check against Alpaca/OpenAI only after deterministic
  tests pass; never run it in pytest or commit its payloads.
- Run:

  ```bash
  uv run ruff format --check .
  uv run ruff check .
  uv run mypy src
  uv run pytest
  ```

## Tradeoffs

- REST polling can be tens of seconds behind a news websocket, but it avoids a
  second blocking transport and gives startup recovery, overlap, pagination, and
  ordinary live behavior one understandable path.
- Per-ticker classification can make more calls for multi-symbol articles, but
  it avoids pretending that relevance or direction is identical for every
  company in a transaction.
- Exact provider-ID/URL deduplication will miss some rewritten syndication. Fuzzy
  semantic clustering would add cost and false-positive risk before live evidence
  shows it is needed.
- A confidence floor is easy to enforce but model confidence is not calibrated
  probability. Replay fixtures and observed false positives should drive later
  tuning.
- A pinned model snapshot improves replay provenance but eventually requires an
  explicit migration decision rather than silently following an alias.

## Implementation gate

This plan and its specification are approved. Implement tasks in order, keep the
fixed transport, classifier, budget, and event-manager boundaries above, and
record any later durable change before expanding scope.
