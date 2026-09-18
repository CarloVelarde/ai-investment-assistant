# Tasks: Research and Reporting

**Document status:** Proposed

**Implementation status:** Not started

This milestone adds live research and reporting. Implementation has not started;
the tasks below describe the required work. Behavior belongs to
[SPEC.md](SPEC.md); implementation approach belongs to [PLAN.md](PLAN.md).

## Tasks

- [x] 1. Prepare the spec, implementation plan, acceptance mapping, and roadmap
  link. Specify report/source contracts, provider ownership, fixed budgets,
  deadline behavior, legacy compatibility, and live-loop limitations.
- [ ] 2. Add typed packet, evidence, report-draft, live-report, and attempt
  contracts. Preserve explicit fake-report compatibility. Test field limits,
  enums, source references, identity validation, and unknown-cause reports.
- [ ] 3. Add the SQLite migration, structured report details, durable attempt
  reservations, deferrals, retry timing, and bounded evidence reads. Test older
  databases, atomic save, persistent budgets, and legacy completed reports.
- [ ] 4. Build local evidence packets and the source registry. Test deterministic
  selection, completed/as-of bars, news provenance, prior-report context,
  missing/revised records, packet limits, and secret exclusion.
- [ ] 5. Add the SEC adapter and optional user-agent setting. Test CIK/submissions
  normalization, filing-ID restrictions, bounded excerpts, redirects, pacing,
  rate-limit deferral, deadline/size limits, and safe unavailable results.
- [ ] 6. Add the separate OpenAI research adapter and prompt/schema version.
  Test pinned model, `store=false`, allowed tools, strict output, built-in search
  caps and sources, bounded continuation, refusal, and safe provider failures.
- [ ] 7. Implement the research runner with durable attempts, deadline and
  call/token limits, duplicate EDGAR handling, one finalization, source-backed
  report validation, and normalized evidence snapshots. Test success, optional
  tool failure, bad references, exhaustion, late results, and replay with fakes.
- [ ] 8. Wire the real researcher into live mode and extend pending processing
  with one attempt per pass, notification-only recovery, fair ordering, and
  restart-safe retry spacing. Test missing keys, exhausted daily budget,
  material updates, interrupted attempts, and stale result rejection.
- [ ] 9. Add post-research market recovery and real console report rendering.
  Test startup/socket handoff, open-to-closed transitions, gap recovery without
  duplicate events, saved-report delivery retry, and retained fake labels.
- [ ] 10. Run end-to-end deterministic scenarios and all repository checks.
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

Specification preparation only. Implementation validation is pending tasks 2–10.
