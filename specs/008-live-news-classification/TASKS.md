# Tasks: Live News and Classification

**Document status:** Complete

**Implementation status:** Complete

Milestone 5 is implemented and validated. Live smoke against Alpaca/OpenAI is
opt-in (`uv run ai-investment-assistant` with keys) and is not part of pytest.

## Tasks

- [x] 1. Resolve the REST-only transport, recovery-window, budget, model,
  confidence, category, and article-revision decisions. Record D-029 and approve
  `SPEC.md`, `PLAN.md`, and this task list.
- [x] 2. Add provenance-complete internal article/classification models and the
  backward-compatible SQLite migration, including durable classification status,
  budgets, and retrieval high-water state. Add migration and restart tests.
- [x] 3. Add the paginated Alpaca REST news boundary and normalization. Prove
  symbol/time parameters, oldest-first recovery, page caps, malformed-sibling
  isolation, retryable failures, and secret-safe diagnostics with a fake HTTP
  provider.
- [x] 4. Add deterministic explicit-watchlist, recency, required-field/source,
  provider-ID, canonical-URL, and per-pass/day budget filtering. Prove rejected or
  deferred candidates make zero classifier calls.
- [x] 5. Add the classifier port, deterministic fake, and OpenAI Responses adapter
  with bounded input, no tools, `store=false`, pinned prompt/model provenance,
  strict structured output, validation, and safe retryable failure handling.
- [x] 6. Convert qualifying positive, negative, and unclear classifications into
  stable `NewsSignal` values and submit them through the unchanged event manager.
  Prove rejected classifications create no signal/event/cooldown and accepted
  news-only or related-news cases promote exactly once.
- [x] 7. Integrate bounded startup recovery and due polling into the existing live
  loop independently of market-session/socket state. Prove overlap, restart,
  classifier failure, stock failure, and pending work remain isolated and
  idempotent.
- [x] 8. Run focused offline and opt-in live smoke checks, update owning documents
  to completed status, confirm no market/event policy drift or later-milestone
  integration, and run all repository checks.

## Acceptance coverage

| Criteria | Tasks |
| --- | --- |
| AC-01 | 2–3 |
| AC-02 | 2, 4 |
| AC-03 | 5 |
| AC-04 | 2, 4–6 |
| AC-05 | 5–6 |
| AC-06 | 2–7 |
| AC-07 | 3, 7 |
| AC-08 | 2–5, 7–8 |
| AC-09 | 1–8 |

## Implementation notes for agents

- Read this folder's approved documents before implementation.
- Keep the news websocket out of the MVP. It is a possible later optimization,
  not part of tasks 2–8.
- Keep the news path separate from market detection and research.
- Use only the explicit user news watchlist; do not accidentally classify news
  for comparison-only `SPY`.
- Persist provider values only after conversion to internal models.
- Keep filter, identity, budget, retry, and promotion decisions deterministic.
- Never send credentials, raw HTML, unrelated article content, private financial
  data, market signals, event history, or database state to the classifier.
- Use fakes in pytest. Never call Alpaca or OpenAI from the test suite.
- Mark tasks complete only after their implementation and tests pass.

Update the owning document before changing product scope or stable architecture.
