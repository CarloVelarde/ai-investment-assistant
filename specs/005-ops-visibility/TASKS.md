# Tasks: Ops Visibility

**Document status:** Approved

**Execution status:** Not started

## Tasks

- [ ] 1. Add `heartbeat` and `watch_log` settings (default false). Prove they load from the environment, stay off by default, and do not change live-mode or secret handling.
- [ ] 2. Emit a regular-logger heartbeat every 60 seconds in the live loop when heartbeat is on, including a closed session with no minutes. Prove heartbeat off stays silent even if watch log is on. Use a fake clock.
- [ ] 3. Add the watch logger and stage lines for a canned live cycle. Prove the narrative appears only when watch log is on, is plain text, and never includes secrets.
- [ ] 4. Document the flags in `.env.example` if anything drifted; run all repository checks.

## Acceptance coverage

| Criteria | Tasks |
| --- | --- |
| AC-01 | 1, 2, 3 |
| AC-02 | 2 |
| AC-03 | 2 |
| AC-04 | 3 |
| AC-05 | 1–4 |

## Implementation notes for agents

- Read this folder’s `SPEC.md` and `PLAN.md` before coding.
- Heartbeat is an on/off flag on the **standard** logger. Do not hide it behind watch log.
- Do not change Milestone 3/4 detector or event rules.
- Use fakes for Alpaca, clock, and time. Never hit the network in pytest.
- Never log credentials.

Mark tasks complete only after validation passes.
