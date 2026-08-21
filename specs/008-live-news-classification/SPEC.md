# Feature Specification: Live News and Classification

**Document status:** Approved

## Purpose

Add the first live news path without changing the market detectors or giving an
AI component control over research. The application should retrieve recent
Alpaca news for the user's configured watchlist, reject obviously ineligible or
duplicate articles with deterministic code, and use one small structured
classifier only for the remaining semantic significance decision.

Significant good, bad, or directionally unclear news may create or enrich a
durable event by itself. Rejected news stops before the event manager and creates
no cooldown. Real research and Discord delivery remain later milestones.

## Scope

### In scope

- Retrieve recent stock news from Alpaca's paginated REST news endpoint at
  startup and during the existing live loop, including while the regular market
  session is closed.
- Query only the user-configured stock watchlist. `SPY` is included only when the
  user explicitly configured it; the comparison-only `SPY` added for market
  context does not automatically create news-classification work.
- Normalize provider payloads immediately into internal article records with a
  stable provider identity, symbols, headline, summary, bounded plain-text
  content, URL, source, creation/update times, and retrieval provenance.
- Persist normalized articles, per-ticker classification attempts and results,
  prompt/model versions, and safe failure state in SQLite.
- Apply deterministic watchlist, recency, required-field, source, exact-identity,
  canonical-URL, and call-budget checks before classification.
- Classify each eligible article/ticker pair with a small model and a strict
  schema containing relevance, category, significance, direction, importance,
  confidence, and a short rationale.
- Treat significance independently of direction. Positive, negative, and unclear
  classifications may all qualify.
- Convert only qualifying classifications into the existing `NewsSignal`
  contract and submit them to the existing `EventManager`.
- Preserve stable identities and restart-safe behavior so the same article,
  classification, and signal are processed once.
- Use fakes for Alpaca news, the classifier, clock/time, research, and
  notification in tests. Pytest performs no network calls.

### Out of scope

- A news websocket, second background loop, thread pool, scheduler, worker,
  service, queue, or multi-agent framework. A news websocket remains a possible
  later optimization if MVP evidence shows REST polling is not timely enough.
- News providers other than Alpaca, broad web monitoring, SEC retrieval, social
  media, analyst notes, or portfolio-specific news.
- Real research, research tools, real notification, Discord, or changes to the
  existing fake research and console-notification stages.
- Letting the classifier judge market-rule signals, correlate events, choose
  research eligibility, or decide notification.
- Changes to market thresholds, session behavior, event-manager correlation,
  cooldown, episode, escalation, or lifecycle rules.
- Fuzzy semantic duplicate clustering, retrospective bulk classification, model
  training, fine-tuning, or autonomous trading.
- User-configurable category policy, prompt editing, or per-symbol significance
  thresholds in this first slice.

## Behavior

### News retrieval and recovery

Poll Alpaca's REST news endpoint from the existing linear live loop. Startup
requests a bounded recent window for the explicit user watchlist, oldest first,
and follows page tokens within a fixed page cap. Later
cycles request from the durable high-water mark with a small overlap. Stable
article identity makes the overlap quiet.

News polling is independent of regular-session state and stock-socket
availability. One failed news request produces a safe diagnostic and does not
stop market ingest, pending event work, or a later retry. One malformed article
is rejected without stopping other articles.

Fixed initial bounds:

- Fresh-database startup lookback: 72 hours, covering a normal weekend.
- Page size: 50, the provider maximum.
- Maximum pages per polling pass: 10.
- Poll no more often than every 30 seconds through the existing live loop.

### Deterministic eligibility

For each normalized article, ordinary code determines which explicitly watched
tickers are candidates. An article/ticker pair stops before AI when it is outside
the recovery window, lacks required safe text/source fields, has no explicit
watched-symbol association, matches an already-processed provider identity or
canonical URL for that ticker, or the fixed classifier budget is exhausted.

Provider article ID is the primary identity. A canonical URL fingerprint catches
the same provider story repeated under a different ID. The first slice treats a
later payload with the same provider article ID as a revision of the saved
article, not new evidence and not a new signal. A separately identified follow-up
article may qualify normally.

Fixed classifier bounds:

- At most 20 classifier calls per polling pass.
- At most 100 classifier calls per UTC day. Successful and failed calls both
  count.
- At most one successful classification per article/ticker/prompt/model version.
- Headline, summary, and stripped content are length-bounded before entering the
  prompt; HTML, scripts, and control text are not passed through as instructions.

### Structured classification

The classifier receives one article and one candidate ticker. It has no tools,
credentials, market detector output, event history, database access, or research
context. Provider text is untrusted data.

The result schema contains:

- `relevant`: whether the story materially concerns the candidate ticker.
- `category`: one controlled event category.
- `significant`: whether the story is important enough to become a news signal.
- `direction`: `UP`, `DOWN`, or `UNCLEAR`.
- `importance`: `MODERATE`, `HIGH`, or `CRITICAL`.
- `confidence`: a value from zero through one.
- `rationale`: a short explanation grounded only in the supplied article.

Controlled categories are `EARNINGS`, `GUIDANCE`, `MERGERS_ACQUISITIONS`,
`REGULATORY_LEGAL`, `PRODUCT_SAFETY`, `MANAGEMENT`, `FINANCING_CAPITAL`,
`OPERATIONS`, `MACRO_SECTOR`, and `OTHER`.

Only `relevant=true`, `significant=true` classifications with confidence at or
above `0.70` become signals. Direction does not gate significance:
`UP` maps to positive/up, `DOWN` maps to negative/down, and `UNCLEAR` maps to the
existing directionless news path.

Use OpenAI's Responses API with strict JSON Schema Structured Outputs and the
pinned `gpt-5.4-nano-2026-03-17` snapshot. The adapter sets
`store=false`, uses no tools, validates the returned internal model again, and
persists the model and prompt version with the classification.

### Promotion and idempotency

A qualifying classification becomes one stable `NewsSignal` for the
article/ticker/classification version. The existing event manager receives that
signal unchanged:

- Significant news may open an event without a market signal.
- Directionally compatible news may enrich an open market event.
- Directionally unclear news follows existing category correlation.
- Significant new news is a material update under existing event rules.
- An exact signal repeat remains quiet.

A rejected filter or classification creates no signal, event, research work,
notification work, or cooldown. Classification transport failure, refusal,
timeout, malformed output, or schema mismatch is saved safely and remains
retryable within the same deterministic budget. Credentials and raw provider or
model failures never enter logs, prompts, fixtures, or exceptions.

## Acceptance criteria

- [ ] AC-01: Alpaca REST pages normalize into validated internal articles with
  stable provider identity, watched symbols, bounded safe text, and complete
  source/retrieval provenance. Malformed articles do not stop valid siblings.
- [ ] AC-02: Watchlist, recency, required-field/source, provider-ID, canonical-URL,
  and fixed call-budget checks happen before the classifier. Filtered articles
  make zero classifier calls.
- [ ] AC-03: The strict classifier schema accepts significant positive, negative,
  and unclear examples and rejects malformed, refused, incomplete, or out-of-
  range output at the adapter boundary.
- [ ] AC-04: Insignificant, irrelevant, low-confidence, duplicate, and
  over-budget candidates are durably explainable but create no signal,
  event, research work, notification work, or cooldown.
- [ ] AC-05: Each qualifying article/ticker pair becomes one stable `NewsSignal`
  with classification and source provenance. News alone can create an event and
  related news can enrich/requeue an existing event through the unchanged event
  manager.
- [ ] AC-06: Startup recovery, page overlap, same-article restart, and bounded
  retry are idempotent. One accepted article produces at most one signal and one
  eligible event update for its classification version.
- [ ] AC-07: News polling and pending event work continue while the regular
  session is closed or the stock socket is unavailable. News failure does not
  stop market processing, and market failure does not invent news.
- [ ] AC-08: Prompt input, output, calls per pass/day, pages, retry, time, and
  stored error detail are bounded. Secrets and private data do not appear in
  logs, prompts, fixtures, exceptions, or test output.
- [ ] AC-09: No news websocket, research model/tool, Discord adapter, worker,
  scheduler, autonomous action, market-rule change, or event-manager policy
  change is added. Ruff, mypy, and pytest pass.

## Constraints

- One local Python process and built-in `sqlite3` only.
- Use Python `>=3.14,<3.15` and `uv`.
- Convert Alpaca and OpenAI values into validated internal models at their
  adapters before core filtering or promotion.
- Keep deterministic filters, budgets, identities, and deduplication outside the
  classifier.
- Keep classifier time, provider I/O, and clocks injectable.
- Detectors/classifiers emit signals only; the event manager alone owns event
  promotion and research eligibility.
- Preserve article, classification, prompt, model, provider, source, URL,
  published/updated, and retrieval provenance where it affects replay.
- Keep provider and model input narrow, read-only, bounded, and
  application-controlled.

## Resolved decisions

- Use REST-only Alpaca news retrieval for the MVP. Keep a news websocket as a
  possible later optimization; see [D-029](../../docs/DECISIONS.md).
- Use a 72-hour fresh-start window, 10-page cap, 30-second cadence, 20-call
  per-pass limit, and 100-call UTC-day limit.
- Use `gpt-5.4-nano-2026-03-17`, `store=false`, no tools, and strict Responses API
  Structured Outputs for the first classifier contract.
- Use the controlled category list above and a fixed `0.70` confidence floor.
- Save same-provider-ID revisions but keep them quiet in this slice. A future
  revision policy requires replay evidence and an explicit spec change.

## Implementation references

- [Alpaca historical news API](https://docs.alpaca.markets/us/reference/news-3)
- [Alpaca real-time news stream](https://docs.alpaca.markets/us/docs/streaming-real-time-news)
- [OpenAI GPT-5.4 nano](https://developers.openai.com/api/docs/models/gpt-5.4-nano)
- [OpenAI Responses API structured output](https://developers.openai.com/api/reference/cli/resources/beta/subresources/responses)
