# Tasks: Discord and Operations

**Document status:** Complete

**Execution status:** Tasks 1–10 complete; Milestone 7 deterministic acceptance checks passed

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
- [x] 8. Wire one external delivery before at most one research run/pass; extend
  minute recovery to delivery failures/timeouts and regular-session transitions.
  Test backlog fairness, next-pass delivery of new reports, and offline draining.
- [x] 9. Extend quiet logs, heartbeat, and watch narration; test secret redaction
  including invalid configuration and HTTP exceptions. Add safe operational status,
  backlog age, and per-kind admission reasons. Update README and `.env.example`
  only for implemented settings/commands and explain migration/recovery limitations.
- [x] 10. Update obsolete milestone scope guards without weakening no-worker,
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

## Completion record — Tasks 8–10, 25 September 2026

- The live loop sends at most one due Discord alert before one research run per
  pass. A new report waits for the next pass. Delivery eligibility now uses the
  saved report time, so a newer report cannot overtake an older backlog item.
  Failed and timed-out sends still trigger minute gap recovery; a session close
  during a send closes the stock socket. Offline processing still drains through
  the existing event-manager path.
- The optional heartbeat reads current report backlog, uncertain and failed work,
  oldest pending age, separate classifier/research admission, and charged,
  reserved, and remaining estimated USD from SQLite. JSON and watch logs record
  delivery and budget transitions. News and HTTP exception text cannot echo a
  webhook or provider token through these paths. README and `.env.example` now
  describe the implemented status and recovery behavior.
- The scope test permits the webhook adapter while guarding against a bot,
  worker, trading code, and event-policy ownership in the adapter. No durable
  product or architecture decision changed. At task completion, full-loop live
  verification remained in Milestone 8; the narrow Discord trial is recorded below.

### Acceptance evidence

The named tests below are in [delivery](../../tests/test_delivery.py),
[Discord adapter](../../tests/test_discord_notify.py),
[notifications](../../tests/test_notifications.py),
[live operations](../../tests/test_milestone7_operations.py),
[main loop](../../tests/test_main.py),
[model budget](../../tests/test_model_budget.py),
[news ingest](../../tests/test_news_ingest.py),
[event policy](../../tests/test_event_manager.py),
[event processing](../../tests/test_event_processing.py),
[research](../../tests/test_research_runner.py),
[ops visibility](../../tests/test_ops_visibility.py), and
[scope guards](../../tests/test_milestone_scope.py).

| Criterion | Passing tests |
| --- | --- |
| AC-01 | `test_success_is_claimed_before_send_and_never_posted_twice`; `test_live_session_delivers_saved_report_through_webhook_sender` |
| AC-02 | `test_unresolved_claim_waits_once_after_restart_and_resends_once`; `test_receipt_saved_before_completion_finishes_locally_after_restart`; `test_completed_delivery_is_not_resent_if_output_fails`; `test_stale_receipt_does_not_mark_new_update_notified` |
| AC-03 | `test_five_definite_failures_use_one_two_four_eight_minute_waits`; `test_fifth_attempt_uncertainty_allows_only_one_sixth_submission`; `test_crash_after_consuming_resend_allowance_does_not_replenish_it`; `test_longer_provider_wait_delays_uncertain_resend`; `test_global_rate_limit_wait_survives_restart_and_changed_webhook` |
| AC-04 | `test_list_is_read_only_and_retry_requires_owner`; `test_list_explains_uncertain_resend_and_operator_review`; `test_manual_retry_is_one_submission_and_respects_global_wait`; `test_confirmation_cancels_pending_resend_and_requires_matching_receipt`; `test_database_lock_releases_when_owner_process_is_killed` |
| AC-05 | `test_offline_fixture_ignores_unused_webhook_setting`; `test_new_console_report_logs_missing_destination_once`; `test_removing_webhook_holds_uncertain_delivery_out_of_console_path`; `test_console_completion_precedes_best_effort_output`; `test_fake_report_keeps_warning_label` |
| AC-06 | `test_same_or_lower_importance_is_saved_without_reopening_event`; `test_higher_importance_requeues_existing_event`; `test_later_important_update_is_immediately_eligible_after_notification`; `test_insignificant_and_irrelevant_results_create_no_event` |
| AC-07 | `test_embed_suppresses_mentions_and_bounds_report_content`; `test_rejects_unsafe_webhook_url_without_echoing_it`; `test_transport_timeout_after_post_is_uncertain`; `test_transport_exception_text_never_reaches_safe_result`; `test_status_excludes_superseded_report_and_news_error_hides_input` |
| AC-08 | `test_research_reserves_before_io_and_releases_unstarted_slots`; `test_interrupted_requests_keep_charge_and_utc_rollover`; `test_missing_search_count_keeps_search_allowance_and_overrun_is_visible`; `test_classifier_count_unknown_usage_cap_reduction_and_overrun`; `test_invalid_model_output_keeps_independent_usage_charge` |
| AC-09 | `test_missing_model_and_budget_make_no_calls_and_no_additional_start`; `test_saved_report_delivers_with_zero_model_budget_and_stale_retry_is_rejected`; `test_heartbeat_status_counts_current_backlog_and_separate_admission` |
| AC-10 | `test_one_send_precedes_research_and_new_report_waits_for_next_pass`; `test_failed_or_timed_out_send_recovers_minutes_after_session_close`; `test_event_manager_defers_new_report_delivery_until_next_pass`; `test_only_latest_waiting_update_is_researched_and_report_precedes_delivery` |
| AC-11 | `test_migration_preserves_console_history_and_holds_unknown_spend`; `test_v6_submission_history_survives_operator_retry_schema_upgrade`; `test_heartbeat_status_counts_current_backlog_and_separate_admission`; `test_heartbeat_stays_off_when_only_watch_log_is_on` |
| AC-12 | `test_webhook_adapter_and_delivery_do_not_own_event_policy_or_trading`; `test_runtime_dependencies_do_not_add_live_or_distributed_services`; four repository checks below; README and `.env.example` review |

### Final repository checks

All four checks passed using Python 3.14 and `uv run --offline` with a writable
temporary `UV_CACHE_DIR`: Ruff format, Ruff lint, mypy (85 source files), and
pytest (552 tests). Tests used fakes and temporary SQLite databases; there were
no live provider calls. The regular commands in AGENTS.md are equivalent when
the default uv cache is writable. Full-loop live-provider results are not claimed.

## Separately scoped live Discord trial — 25 September 2026

On a test branch, a one-off runner used the real configured Discord webhook with
an isolated temporary SQLite database and one saved `TEST` fixture report labeled
`FAKE RESEARCH — NOT INVESTMENT ANALYSIS`. The app ran its normal live delivery
path with fake market data and no OpenAI key or research call. The real webhook
accepted one POST; SQLite recorded `SUCCEEDED`, one submission, a numeric message
receipt, and `NOTIFIED` for the matching event. A read-only Discord message lookup
verified the logical delivery ID in the footer. Restarting the app against the
same database left the submission count at one and the event notified.

The run did not exercise real Alpaca, OpenAI, SEC, provider failure/retry, uncertain
delivery, or the complete signal-to-report loop. Those broader live checks remain
under [spec 011](../011-full-loop-hardening/SPEC.md). No webhook URL, token, or
message ID is recorded here.
