# Tasks: Discord and Operations

**Document status:** Refined draft

**Execution status:** Specification review complete; implementation not started

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

- [ ] 2. Add logical delivery/attempt models, destination/global state, local
  single-owner lock, and atomic schema migration. Preserve legacy console successes,
  historical failures, counts, and earlier migration paths. Test migration-day
  unknown-spend hold and lock recovery after process exit.
- [ ] 3. Implement secret webhook settings, URL validator, pure embed builder,
  bounded HTTP, receipt parsing, mention suppression, and safe error mapping. Test
  malformed/oversized 2xx, 5xx uncertainty, redirects, and total deadlines.
- [ ] 4. Implement pre-send claim, receipt recovery, current-update completion,
  stable logical ids, finite retries, provider/destination/global waits, disabled
  destinations, and supersession. Persist the 15-minute uncertain-resend deadline
  and consume its one-time allowance with the claim before I/O. Test every crash
  boundary/refused write, deadline boundary, longer provider waits, a fifth-attempt
  uncertain result, resend failure/uncertainty, and no allowance reset on restart.
- [ ] 5. Implement read-only notification listing, explicit one-send retry, and
  receipt confirmation commands. Test command replay, ownership, rate-limit holds,
  invalid receipts, stale updates, and webhook changes without direct SQL recovery.
  Reject manual retries while automatic resend is pending; confirmation cancels it.
- [ ] 6. Preserve console/offline behavior with explicit destination and durable
  completion before best-effort output. Test fake labeling, no silent fallback,
  no back-catalog sends, and unchanged routine/material/new-episode eligibility.
- [ ] 7. Implement shared microdollar ledger, configured caps, atomic reservations,
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
