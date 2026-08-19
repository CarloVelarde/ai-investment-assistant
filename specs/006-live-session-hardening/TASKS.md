# Tasks: Live Session Hardening

**Document status:** Approved

**Execution status:** Complete

This slice is complete. Milestone 5 has not started and requires its own approved spec.

## Tasks

- [x] 1. Issue 1: treat today’s still-open daily row as incomplete. Do not run `multi_day_move`, `drawdown_from_high`, or `relative_to_spy` on it. Prove an unfinished today row that would cross a daily threshold does not emit or save daily “already emitted” state.
- [x] 2. Issue 1: prove a second backfill in the same open session with a moved still-moving close does not create or escalate a daily event (TSLA vs `SPY` shape). Prove that after the clock is closed, the finished today daily bar may emit.
- [x] 3. Issue 2: add `session_gap` / `SESSION_OPEN` (3% / 5% / 8%, rearm 1.5%, no volume dampen). Unit-test +6.67% → HIGH UP, +2% quiet, −3% → MODERATE DOWN, Friday close → Monday open.
- [x] 4. Issue 2: run the gap check once per symbol per session after backfill and/or on the first regular-session minute. Prove a 10:25 start still fires from the stored 09:30 open, and a same-session restart does not research again. Wire it on the live path only; offline fixture mode stays as it is unless a tiny fixture is needed for the unit tests.
- [x] 5. Confirm Milestone 3 fast/daily thresholds are unchanged; no news client, Discord, workers, or second socket; update `TASKS.md`; run all repository checks.

## Acceptance coverage

| Criteria | Tasks |
| --- | --- |
| AC-01 | 1 |
| AC-02 | 2 |
| AC-03 | 2 |
| AC-04 | 3, 4 |
| AC-05 | 3 |
| AC-06 | 4 |
| AC-07 | 5 |

## Implementation notes for agents

- Read this folder’s `SPEC.md` and `PLAN.md` before coding. Record durable choice changes in `docs/DECISIONS.md` first (D-026 and D-027 already do).
- Add tests with each task. Mark a task complete only after its tests pass.
- Use fakes for Alpaca, clock, and time. Never hit the network in pytest.
- Do not change Milestone 3 threshold numbers for `abrupt_move`, `multi_day_move`, `drawdown_from_high`, or `relative_to_spy`.
- Do not open the news websocket.

Mark tasks complete only after validation passes.
