# Tasks: Regular Session Lifecycle Correction

**Document status:** Approved

**Execution status:** In progress — Tasks 1 and 2 complete

Milestone 5 remains not started until this corrective slice is implemented and
validated.

## Tasks

- [x] 1. Enforce the regular-minute invariant across REST replay, stream ingest,
  gap fill, storage, and fast-detector history. Ignore new non-regular minutes;
  prove 15:59 ET is accepted, 16:00 ET is rejected, and existing extended-hours
  rows cannot contaminate a later regular one-hour window.
- [x] 2. Make the live loop session-aware without adding a scheduler: process
  startup recovery and pending events independently, open/consume/reconnect the
  one stock socket only while the provider session is open, close it when closed,
  and allow a closed start to connect at the next open. Use explicit connection
  state, do not call the auto-opening iterator while closed, and avoid a busy
  loop. Add fake transition and closed-start tests.
- [ ] 3. Run one REST minute gap fill immediately after an open-session
  initial subscription. Do not duplicate the reconnect path’s existing gap fill.
  Prove a minute completed during startup is recovered and overlap with a
  buffered stream frame does not duplicate bars, signals, research, or
  notification.
- [ ] 4. Allow the latest completed daily session to emit once during startup
  recovery even after the calendar date changes. Prove next-morning and weekend
  catch-up, same-bar restart quiet, and no late prior-session fast or session-gap
  signal.
- [ ] 5. Run focused restart and session-transition dry runs, update the owning
  documents to completed status, confirm thresholds and event-manager behavior
  are unchanged, and run all repository checks.

## Acceptance coverage

| Criteria | Tasks |
| --- | --- |
| AC-01, AC-02 | 1 |
| AC-03, AC-04, AC-07 | 2 |
| AC-05 | 3 |
| AC-06 | 4 |
| AC-08 | 1–5 |

## Implementation notes for agents

- Read this folder’s `SPEC.md` and `PLAN.md` before editing code.
- Keep the main loop linear and use the existing provider/session boundaries.
- Do not add an exchange calendar, scheduler class, background worker, second
  socket, news client, or extended-hours storage model.
- Do not change market thresholds or event-manager promotion rules.
- Use fakes for Alpaca, clock, time, research, and notification. Never hit the
  network in pytest.
- Mark tasks complete only after their implementation and tests pass.

Mark tasks complete only after validation passes. Update the owning document
before changing scope or architecture.
