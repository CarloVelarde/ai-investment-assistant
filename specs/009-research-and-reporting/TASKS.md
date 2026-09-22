# Tasks: Research and Reporting

**Document status:** Complete

**Implementation status:** Complete

This milestone adds live research and reporting. Tasks 1–10 are complete.
Live smoke against Alpaca, OpenAI, and SEC is opt-in and is not part of pytest.
No live smoke was run for this closeout.
Behavior belongs to
[SPEC.md](SPEC.md); implementation approach belongs to [PLAN.md](PLAN.md).

## Tasks

- [x] 1. Prepare the spec, implementation plan, acceptance mapping, and roadmap
  link. Specify report/source contracts, provider ownership, fixed budgets,
  deadline behavior, legacy compatibility, and live-loop limitations.
- [x] 2. Add typed packet, evidence, report-draft, live-report, and attempt
  contracts. Preserve explicit fake-report compatibility. Test field limits,
  enums, source references, identity validation, and unknown-cause reports.
- [x] 3. Add the SQLite migration, structured report details, durable attempt
  reservations, deferrals, retry timing, and bounded evidence reads. Test older
  databases, atomic save, persistent budgets, and legacy completed reports.
- [x] 4. Build local evidence packets and the source registry. Test deterministic
  selection, completed/as-of bars, news provenance, prior-report context,
  missing/revised records, packet limits, and secret exclusion.
- [x] 5. Add the SEC adapter and optional user-agent setting. Test CIK/submissions
  normalization, filing-ID restrictions, bounded excerpts, redirects, pacing,
  rate-limit deferral, deadline/size limits, and safe unavailable results.
- [x] 6. Add the separate OpenAI research adapter and prompt/schema version.
  Test pinned model, `store=false`, allowed tools, strict output, built-in search
  caps and sources, bounded continuation, refusal, and safe provider failures.
- [x] 7. Implement the research runner with durable attempts, deadline and
  call/token limits, duplicate EDGAR handling, one finalization, source-backed
  report validation, and normalized evidence snapshots. Test success, optional
  tool failure, bad references, exhaustion, late results, and replay with fakes.
- [x] 8. Wire the real researcher into live mode and extend pending processing
  with one attempt per pass, notification-only recovery, fair ordering, and
  restart-safe retry spacing. Test missing keys, exhausted daily budget,
  material updates, interrupted attempts, and stale result rejection.
- [x] 9. Add post-research market recovery and real console report rendering.
  Test startup/socket handoff, open-to-closed transitions, gap recovery without
  duplicate events, saved-report delivery retry, and retained fake labels.
- [x] 10. Run end-to-end deterministic scenarios and all repository checks.
  Document any opt-in live smoke separately, update owning docs and completion
  status only from evidence, and retain later-milestone scope guards.

## Acceptance coverage

| Criteria | Tasks | Required evidence |
| --- | --- | --- |
| AC-01 | 2–4 | Bounded local packet with provenance and no secrets. |
| AC-02 | 2–4, 6–9 | Valid source-backed live report saved before console output. |
| AC-03 | 2, 4, 7 | Packet-only market event can honestly report unknown cause. |
| AC-04 | 7–10 | News-only and combined events preserve existing promotion. |
| AC-05 | 5–7 | Optional tools fail safely without preventing a valid report. |
| AC-06 | 6–7 | Search/function accounting and exactly one capped finalization. |
| AC-07 | 3, 6–8 | Failure/deferred paths make no report or notification. |
| AC-08 | 3, 7–9 | Restart resumes the correct stage; one saved report per update. |
| AC-09 | 6, 8–10 | Offline fakes remain; live failures never create fake reports. |
| AC-10 | 8–10 | Event policy retained; all repository checks pass. |
| AC-11 | 3, 8–9 | Fair bounded live processing and post-research gap recovery. |
| AC-12 | 3, 7–9 | Attempts, budgets, evidence, and migration survive restart. |
| AC-13 | 2, 4–7 | Invalid citations/tool targets and size/time overruns rejected. |

## Implementation notes

- Implement tests with each behavior; use fakes for model, SEC, market, news,
  notifications, UTC/elapsed time, and waiting. Pytest must never call providers.
- Follow the spec's tool ownership: hosted search is executed by OpenAI; EDGAR
  functions are executed and deduplicated by the application.
- Keep event eligibility, deduplication, promotion, and cooldowns outside AI.
- Keep the existing fake for offline fixtures and explicit test injection only;
  preserve historical fake reports without silently upgrading or resending them.
- Do not add Discord, brokerage, another socket, a worker, or a scheduler.
- Do not mark tasks complete until their implementation and tests pass. Keep
  manual live checks distinct from deterministic automated coverage.

## Validation record

Tasks 2–4 validated on 18 September 2026 with deterministic local tests:

- Closed report schemas, field limits, unknown-cause reports, citation and identity
  validation, and explicit fake-report compatibility.
- SQLite version 5 migration, including supported versions 1–4; legacy completed
  reports stay completed. Attempt reservations, daily budget, retry spacing,
  deferrals, fair selection, and snapshots survive restart.
- Atomic report/attempt save, transaction rollback, stale-update rejection, and
  saved-report delivery retry through the existing event manager.
- Bounded deterministic signals, completed/as-of bars, linked news and classifier
  versions, later revisions, missing records, prior fake context, packet trimming,
  required-content overflow, comparison-symbol deduplication, and secret exclusion.

Repository checks: `uv run ruff format --check .`, `uv run ruff check .`,
`uv run mypy src`, and `uv run pytest`. A writable temporary `UV_CACHE_DIR`
is used in the sandbox. All four checks passed: 104 files formatted, Ruff clean,
mypy clean across 24 source files, and 382 tests passed (26 new research tests).
No live smoke or provider calls were performed. Full milestone acceptance,
including production live research, remains pending tasks 8–10.


Tasks 5–7 validated on 18 September 2026:

- SEC ticker caching, bounded recent filings, approved filing IDs, markup stripping,
  partial excerpts, redirects, safe errors, request pacing, Retry-After deferral,
  and shared/per-request deadlines; optional user-agent configuration.
- Pinned Responses request, strict schema, stateless opaque continuation, all
  output items, normalized web citations, refusals, malformed output, token/body
  limits, excess hosted calls, and safe provider failures.
- Packet-only unknown cause, positive news-only and combined events, SEC and web
  evidence, report-before-notify, durable snapshots, and restart behavior through
  the existing event manager using fake HTTP.
- Duplicate and invalid functions, matching results for denied call IDs, shrinking
  search budgets, first-cap finalization, four tool turns plus one final turn,
  no extra retry, missing keys, exhausted starts, stale results, input overflow,
  late packet/model/tool results, and failure usage audit.
- No raw provider errors, HTML, opaque reasoning, or API credentials are stored
  as research evidence. No live provider calls or live smoke were performed.

All four repository checks passed for tasks 5–7: 111 files passed format checks,
Ruff was clean, mypy passed across 27 source files, and all 454 tests passed
(72 additional tests since tasks 2–4). `git diff --check` also passed.

Tasks 8–9 validated on 18 September 2026 with deterministic local tests:

- Live `process_pending` delivers every saved report first, then starts at most
  one fair research run. Offline drain behavior is unchanged.
- Missing OpenAI keys and exhausted daily budgets defer without a provider call,
  fake report, notification, or extra failure on later passes.
- Interrupted attempts wait five minutes from recorded start; a newer material
  update is immediately eligible; stale results are discarded.
- Production live mode constructs `ResearchRunner` while storage is open. Live
  mode without an OpenAI key writes no fake report. Injected test researchers
  remain available.
- Post-research REST minute recovery runs without reconnecting a healthy socket.
  Startup research plus the post-subscription handoff, and research that crosses
  the regular close, create no duplicate events or reports.
- Console output includes posture, uncertainty, and a compact source list for
  live reports, and keeps the explicit fake label for fixture reports. Saved
  reports retry delivery without a second research run.

All four repository checks passed for tasks 8–9: 112 files passed format checks,
Ruff was clean, mypy passed across 27 source files, and all 467 tests passed
(13 additional tests since tasks 5–7). `git diff --check` also passed. No live
smoke or provider calls are part of pytest. Full milestone closeout remains
task 10.

Task 10 validated on 21 September 2026 with deterministic local tests:

- Significant positive, negative, and unclear news each produce one live report
  for the current update without a market signal. Unclear news keeps a category
  and no direction. Injected article text is sent as untrusted evidence and does
  not become a source or change the ticker.
- A market signal plus significant same-direction news stays one event and
  produces one report for the current update.
- A `relative_to_spy` comparison can report broad-market scope with
  `cause_unknown` and no invented company cause.
- Exact repeats and same-severity market continuation do not start another
  research run. Significant news on a notified event still requeues one new
  update and one new report.
- An article that tells the model to research another ticker and cite an
  arbitrary filing URL does not cause an SEC request. The invented citation is
  rejected, no report is saved, and nothing is notified.
- Scope guards still exclude Discord, brokerage, workers, schedulers, and the
  news websocket. Detectors and the news classifier do not own research or
  event promotion. The event manager does not call the research providers.

Opt-in live smoke, recorded separately: not run. Pytest made no Alpaca, OpenAI,
or SEC calls. A manual smoke would need a disposable database and real keys and
is not evidence for the checks above.

All four repository checks passed for task 10: 113 files passed format checks,
Ruff was clean, mypy passed across 27 source files, and all 473 tests passed
(6 additional tests since tasks 8–9). `git diff --check` also passed.
