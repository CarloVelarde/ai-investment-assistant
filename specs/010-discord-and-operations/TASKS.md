# Tasks: Discord and Operations

**Document status:** Refined; implementation in progress

**Execution status:** Tasks 2–7 implemented and tested; Milestone 7 remains open

Behavior: [SPEC.md](SPEC.md). Approach: [PLAN.md](PLAN.md). Checkbox completion
requires the named behavior and its tests, not merely a helper or schema field.

## Specification review

- [x] 1. Compare the draft with SQLite v5, console notification, live scheduling,
  research reservations, classifier counts, and existing tests. Refine acceptance
  criteria and task mapping; align permanent decisions/product/architecture/roadmap.
  Separate Milestone 8 into spec 011. This is documentation work, not implementation.
- [x] Record the user's choice: one automatic resend after 15 minutes for uncertain
  Discord delivery, accepting possible duplicates, then operator recovery if that
  resend cannot complete. Align both milestone specs and permanent documents.

## Implementation

- [x] 2. Add logical delivery/attempt models, destination/global state, local
  single-owner lock, and atomic schema migration. Preserve legacy console successes,
  historical failures, counts, and earlier migration paths. Test migration-day
  unknown-spend hold and lock recovery after process exit.
- [x] 3. Implement secret webhook settings, URL validator, pure embed builder,
  bounded HTTP, receipt parsing, mention suppression, and safe error mapping. Test
  malformed/oversized 2xx, 5xx uncertainty, redirects, and total deadlines.
- [x] 4. Implement pre-send claim, receipt recovery, current-update completion,
  stable logical ids, finite retries, provider/destination/global waits, disabled
  destinations, and supersession. Persist the 15-minute uncertain-resend deadline
  and consume its one-time allowance with the claim before I/O. Test every crash
  boundary/refused write, deadline boundary, longer provider waits, a fifth-attempt
  uncertain result, resend failure/uncertainty, and no allowance reset on restart.
- [x] 5. Implement read-only notification listing, explicit one-send retry, and
  receipt confirmation commands. Test command replay, ownership, rate-limit holds,
  invalid receipts, stale updates, and webhook changes without direct SQL recovery.
  Reject manual retries while automatic resend is pending; confirmation cancels it.
- [x] 6. Preserve console/offline behavior with explicit destination and durable
  completion before best-effort output. Test fake labeling, no silent fallback,
  no back-catalog sends, and unchanged routine/material/new-episode eligibility.
- [x] 7. Implement shared microdollar ledger, configured caps, atomic reservations,
  classifier pre-call counting/output cap, and independent usage extraction.
  Test failures, invalid/stale output, interrupted requests, missing usage/search
  counts, full-run release, overrun, unknown pricing, UTC rollover, cap reductions,
  settings validation, and saved-report delivery during model deferral.
- [ ] 8. Wire one external delivery before at most one research run/pass; extend
  minute recovery to delivery failures/timeouts and regular-session transitions.
  Test backlog fairness, next-pass delivery of new reports, and offline draining.
- [ ] 9. Extend quiet logs, heartbeat, and watch narration; test secret redaction
  including invalid configuration and HTTP exceptions. Add safe operational status,
  backlog age, and per-kind admission reasons. Update README and `.env.example`
  only for implemented settings/commands and explain migration/recovery limitations.
- [ ] 10. Update obsolete milestone scope guards without weakening no-worker,
  no-bot, no-trading, or decision-boundary rules. Run all repository checks, link
  each AC to passing tests, then mark Milestone 7 complete and hand off to spec 011.

## Acceptance coverage

| Criteria | Tasks | Evidence to record at completion |
| --- | --- | --- |
| AC-01 | 2, 3, 4 | Normal delivery/restart POST counts and durable receipt |
| AC-02 | 2, 4, 6 | Crash matrix, stale update, refused write, output gap |
| AC-03 | 3, 4 | Ordinary attempt limit, one 15-minute uncertain resend, restart/failure bounds, provider waits |
| AC-04 | 2, 4, 5 | Ownership, recovery commands, destination transitions |
| AC-05 | 3, 6 | Offline/console/configured-failure matrix |
| AC-06 | 4, 6 | Repeat, material escalation, new episode, rejected news |
| AC-07 | 3, 9 | Payload, URL, response/deadline, redaction boundaries |
| AC-08 | 2, 7 | Durable count/cost fault and rollover matrix |
| AC-09 | 6, 7, 9 | Per-kind deferrals and delivery independent of budget |
| AC-10 | 8 | Live-loop fairness/recovery/session integration |
| AC-11 | 2, 7, 9 | Migration, accounting hold, truthful status |
| AC-12 | 9, 10 | User docs, scope guards, complete check output |

## Validation record — documentation review, 22 September 2026

Pre-merge checks passed on macOS with Python 3.14.7. These results do not satisfy
any implementation AC:

- `uv run --offline ruff format --check .` — 119 files already formatted.
- `uv run --offline ruff check .` — passed.
- `uv run --offline mypy` — passed, 76 source files.
- `uv run --offline pytest` — 473 passed.
- `uv lock --check --offline` — passed.
- `uv build --offline` — source distribution and wheel built successfully in a
  temporary output directory.

Commands used a writable temporary `UV_CACHE_DIR`; code and dependencies were
unchanged. Local Markdown link/whitespace checks passed across the eleven reviewed
documents; every spec 010/011 acceptance criterion has a task mapping. Remote CI
and live-provider behavior were not exercised. All checks above were rerun after
the resend-policy clarification and roadmap cleanup; implementation remains pending.

No live Discord/OpenAI/Alpaca/SEC requests are part of this review. Public provider
documentation was consulted for the contract, not to verify account access.

## Implementation record — Tasks 2–4, 23 September 2026

- SQLite v6 adds one logical delivery per event/update, append-only HTTP submission
  outcomes, destination/global wait state, and a local process-owner lock. Migration
  retains old console attempts and successes. A migration day with legacy model
  calls holds new research and classification until the next UTC day.
- The live Discord path validates the secret webhook, builds a bounded embed,
  claims before HTTP, saves a numeric receipt before local completion, and retains
  retry/uncertainty state across restarts. Blank webhook and offline fixtures keep
  the console path. An uncertain prior Discord delivery cannot become console
  success when a webhook is removed.
- `tests/test_delivery.py` covers v5 migration and old history, migration-day hold,
  lock ownership after process exit, claim refusal, receipt recovery, stale updates,
  ordinary backoff, the 15-minute resend, fifth-attempt uncertainty, a consumed
  resend after crash, provider waits, destination changes, and disabled endpoints.
  `tests/test_discord_notify.py` covers URL secrecy, payload limits, mention
  suppression, links, HTTP status mapping, deadline phases, and bounded reads.
  `tests/test_main.py` covers the live-session wiring with a fake sender.
- The four required repository checks passed with 527 tests. Tests used fakes and
  temporary SQLite files. No live Discord alert or other provider call was made.
  At that point Tasks 5–10 and the remaining acceptance criteria were open; a live smoke and
  full-loop verification are separate from these deterministic checks.

## Implementation record — Tasks 5–7, 24 September 2026

- SQLite v7 retains v6 delivery history, permits separately authorized operator
  submissions beyond the automatic six-attempt bound, records each submission's
  destination fingerprint, and adds shared microdollar run/request reservations.
  A v5/v6 upgrade with same-day legacy calls holds new model use until the next
  UTC day rather than inventing prior spend.
- `notifications list` reads SQLite without initialization or provider calls.
  `retry` records one extra send for live processing under the local owner lock;
  `confirm` fetches a Discord message and verifies the logical delivery footer.
  Global/provider waits, stale updates, uncertain resend priority, and restored
  webhook receipts stay guarded. Console completion is saved before best-effort
  output; saved-report delivery does not depend on a model key or budget.
- Production research and news classification reserve configured counts and
  estimated cost before model I/O. Research requests are recorded before each
  call; validated usage settles independently of report/classification success.
  Missing usage or interrupted calls retain their allowance, unused research
  requests release it, and an overrun stops new model starts for that UTC day.
  The classifier request now caps output at 2,000 tokens.
- Recovery, migration, console, and delivery cases are in `tests/test_delivery.py`,
  `tests/test_discord_notify.py`, and `tests/test_notifications.py`. Ledger,
  invalid/stale output, pre-call counting, settings, rollover, and interruption
  cases are in `tests/test_model_budget.py`, `tests/test_research_runner.py`,
  `tests/test_news_ingest.py`, and `tests/test_news_classifier.py`.
  `README.md` and `.env.example` describe the implemented commands and limits.
- The four required repository checks passed: format, Ruff, mypy, and 544 tests.
  All provider interactions in tests use fakes. Tasks 8–10, expanded operational
  status, full Milestone 7 acceptance review, and Milestone 8 live verification
  remain open.

## Handoff for Tasks 8–10

- Start with [SPEC.md](SPEC.md) and [PLAN.md](PLAN.md). Task 8 already has a partial
  path: `EventManager.process_pending` attempts one Discord delivery before its
  capped research run, and `main._process_pending_events` tracks external work.
  Prove or correct backlog fairness, next-pass delivery, minute recovery after
  send failure/timeout, and regular-session transitions. Keep offline draining.
- For Task 9, `README.md` and `.env.example` already describe implemented commands
  and model limits. `ops_log.py` and the heartbeat call sites still lack the
  requested delivery backlog, age, destination, cost, and per-kind deferral status.
  Add transition logs and safe status without printing webhook URLs or report bodies.
- For Task 10, `tests/test_milestone_scope.py` already permits the intended webhook
  adapter while retaining no-bot/no-worker/no-trading guards. Finish the acceptance
  evidence matrix, run all required checks, then update Milestone 7 status only if
  AC-01–11 pass. Milestone 8 live verification remains a separate gate.
