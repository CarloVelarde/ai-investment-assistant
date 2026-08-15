# Tasks: Live Market Data

**Document status:** Approved

**Execution status:** In progress

## Tasks

- [x] 1. Extend settings for Alpaca key id, secret, feed (`iex` default), trading URL, and watchlist. Prove missing keys stay offline, secrets are not logged, `SPY` is always watched, and more than 30 symbols including `SPY` is rejected.
- [x] 2. Map Alpaca-shaped REST/stream bar payloads to `MarketBar` (UTC times, `1Min`/`1Day` end times, provider/feed/retrieved_at). Add validation tests with no network.
- [x] 3. Add a market-data port and an in-memory fake (history pages, stream minutes, clock open/close). Production code talks to the port only.
- [x] 4. Implement REST history fetch with pagination and safe 429 backoff behind the port. Test multi-symbol pages and a SIP-permission failure diagnostic.
- [x] 5. Implement startup backfill into existing bar storage and quiet detector replay before the live cutoff. Test old bars update state but do not research; a cutoff-or-later qualifying bar can emit.
- [x] 6. Implement the stock stream adapter for completed `bars` and `updatedBars`. Test regular-session filter, first crossing, quiet continuation, and revision replace/re-evaluate.
- [x] 7. After regular close (fake clock), fetch completed `1Day` bars and run the existing daily detector plus episode maintenance. Test that streaming-style running daily bars are not treated as complete.
- [x] 8. Implement disconnect → backoff → resubscribe → REST gap fill. Test the gap is filled and a crossing already in detector state does not research again.
- [x] 9. Implement stale-stream rules (socket silence vs one missing illiquid minute). Test diagnostics and that a single skipped IEX minute is not fatal.
- [x] 10. Wire `main`: live loop when keys exist, existing abrupt-drop fixture path when they do not. Add `.env.example`. Keep one process.
- [ ] 11. Confirm no news client, Discord, workers, ORM, or weekly cadence were added; update `docs/DECISIONS.md` only if a durable choice changed; run all repository checks.

## Acceptance coverage

| Criteria | Tasks |
| --- | --- |
| AC-01 | 1, 10 |
| AC-02 | 2–3 |
| AC-03 | 4–5 |
| AC-04 | 5 |
| AC-05 | 6, 8 |
| AC-06 | 7 |
| AC-07 | 8 |
| AC-08 | 9 |
| AC-09 | 1, 6 |
| AC-10 | 10–11 |

## Implementation notes for agents

- Read this folder’s `SPEC.md` and `PLAN.md` before coding. Do not change Milestone 3 thresholds.
- Add tests with each task. Mark a task complete only after its tests pass.
- Use fakes for Alpaca, clock, and time. Never hit the network in pytest.
- Convert provider objects to `MarketBar` before storage or detection.
- Do not open the news websocket.
- If a durable product/architecture choice must change, update `docs/DECISIONS.md` first.

Mark tasks complete only after validation passes. Update the owning document before changing scope or architecture.
