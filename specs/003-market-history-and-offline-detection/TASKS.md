# Tasks: Market History and Offline Detection

**Document status:** Approved

**Execution status:** Not started

## Tasks

- [ ] 1. Add `MarketBar` (and timeframe enum) with validation tests for required fields, UTC times, and complete-bar expectations.
- [ ] 2. Extend SQLite schema/version for market bars, detector state, and `episode_open` / `closed_at` on events. Prove setup is safe to repeat and data survives close/reopen.
- [ ] 3. Implement idempotent bar save/load by stable bar identity; test duplicate ingest does not multiply rows.
- [ ] 4. Implement pure metric helpers and threshold tables (fast one-hour move, multi-day returns, drawdown, relative-to-`SPY`, volume dampening, rearm line). Cover boundaries with unit tests.
- [ ] 5. Implement detector state load/save and the cross / escalate / quiet / rearm state machine with unit tests (no event manager yet).
- [ ] 6. Implement the fast detector over completed minute bars; test trigger, non-trigger, continuation quiet, escalation, and volume dampening.
- [ ] 7. Implement the daily detector (multi-day, drawdown, relative-to-`SPY`); test each rule, missing-`SPY` skip behavior, and importance mapping.
- [ ] 8. Restrict market event grouping to open episodes; close an episode when all detector keys for that ticker+direction are clear after daily evaluation. Test close does not research and a later drop creates a new event.
- [ ] 9. Add offline bar fixtures for abrupt drop, gradual decline, continuation, escalation, recovery, and broad-market/`SPY` scenarios.
- [ ] 10. Wire offline pipeline: ingest bars in time order → detect → handle_signal → episode maintenance → process_pending. Replace console reliance on the Milestone 1 toy market rule with history-based detection for this path.
- [ ] 11. Add end-to-end scenario tests proving AC-06–AC-10 (events, single latest research on escalation, recovery close, new event after close, relative-to-`SPY` behavior) with controllable clock and temporary DB.
- [ ] 12. Confirm no live services, workers, ORM, or configurable cadence were introduced; update permanent docs only if implementation forces a durable decision change; run all repository checks.

## Acceptance coverage

| Criteria | Tasks |
| --- | --- |
| AC-01 | 2–3 |
| AC-02 | 4–6 |
| AC-03 | 4, 7 |
| AC-04 | 5–7 |
| AC-05 | 2, 5, 11 |
| AC-06 | 9–11 |
| AC-07 | 8, 10–11 |
| AC-08 | 8, 9, 11 |
| AC-09 | 8, 11 |
| AC-10 | 7, 9, 11 |
| AC-11 | 9–12 |
| AC-12 | 12 |
| AC-13 | 12 |

## Implementation notes for agents

- Read `SPEC.md` and `PLAN.md` before coding; do not invent alternate thresholds.
- Add tests with each task; mark a task complete only after its tests pass.
- Prefer pure functions for metrics; keep SQLite behind `storage.py` safe lifecycle/write methods.
- Detectors must not call research or notification APIs.
- Use fakes and temporary databases; never hit the network.
- If a durable product/architecture choice must change, update `docs/DECISIONS.md` (and owners) before code.

Mark tasks complete only after validation passes. Update the owning document before changing scope or architecture.
