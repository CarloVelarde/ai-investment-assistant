# Feature Specification: Full-loop Hardening

**Document status:** Draft — completion contract defined

**Milestone:** 8 — Full-loop hardening

**Execution status:** Not started; depends on implemented spec 010

[PLAN.md](PLAN.md) owns the approach; [TASKS.md](TASKS.md) owns execution/evidence.
The goal is to prove the local MVP's existing loop and document its limits, not add
new indicators, agents, or services. [PRODUCT.md](../../docs/PRODUCT.md#mvp-success-criteria)
owns success criteria; [ROADMAP.md](../../docs/ROADMAP.md) owns readiness status.

## Purpose

Demonstrate that meaningful market moves and significant news independently become
durable events, bounded cited reports, and useful Discord alerts, while routine
repeats remain quiet and interrupted work recovers honestly. Test the same boundaries
used by live operation. A separate demo that bypasses the real scheduler, storage,
adapters, or report validation does not prove the full loop.

Milestones 0–6 have implementation and deterministic test evidence; market-only
live trials are recorded in the roadmap. There is no recorded full-loop trial of
news classification, research, and Discord. Milestone 7 must implement delivery and
budget controls before the end-to-end gate can be closed. Existing tests are useful
building blocks, not proof that all scenarios below are already integrated.

## Scope and prerequisites

Start full-loop execution after all spec 010 acceptance criteria pass. Reuse current
Python/uv, SQLite, fake providers/clocks, narrow research tools, and report schema.
Add deterministic scenarios, targeted bug fixes with regressions, operator guidance,
and bounded live evidence. Each change must address a reproduced failure or a listed
acceptance gap; record behavior changes in the owning decision/spec first.

No new news websocket, exchange-calendar service, worker, deployment, UI, portfolio
features, strategy, or autonomous execution. Calendar/data gaps discovered here need
either a small justified correction or an explicit limitation that still permits
the stated success criteria. A limitation cannot waive broken core behavior.

## Replay contract

Use fake provider/HTTP/model responses at the real application input boundaries,
then actual normalization, detectors, news filters/classifier-output validation,
event manager, SQLite, research runner, report validation, delivery scheduler, and
budget accounting. A fake model returns structured outputs and observed source
metadata; do not replace the whole pipeline with a prebuilt report in every test.
Use real report construction for representative end-to-end paths. Adapter contract
tests can complement, rather than duplicate, each long scenario.

Every scenario records a named fixture, fixed timestamps, settings/feed/model/prompt
and price-policy versions, expected signal/event/update ids or counts, reports,
provider-call counts, delivery outcomes, costs/reservations, and recovery state.
Reopen the same temporary SQLite file for restart tests. Replaying settled inputs
must not create more signals, research, successful deliveries, or charges. Crash
injection points distinguish remote side effects from local durable writes.

Commit synthetic or sanitized redistributable fixtures only. Keep credentials,
webhook URLs, private holdings/notes, and raw account data out of test assets and
reports. Network/model clocks and waits are controlled; pytest needs no secrets,
external services, wall-clock market session, or provider billing.

## Required scenario matrix

| ID | Scenario | Required observation |
| --- | --- | --- |
| S01 | Abrupt rise and abrupt drop, no related news | Each direction can qualify, research, and notify; no news gate or invented cause. |
| S02 | Up/down session-open gaps; intraday restart; unfinished current daily | One gap evaluation/session, no repeated work on restart, no premature daily signal. |
| S03 | Gradual five-/twenty-day movement, high drawdown, relative-to-SPY movement | Shared pipeline, understandable crossing context, bounded history; no separate weekly process. |
| S04 | Continuation, severity increase, new horizon, recovery then fresh breach | Routine evidence remains one quiet episode; material update requeues once; recovery allows a distinct episode. |
| S05 | Significant positive, negative, and unclear news without price trigger | Structured classification can create an event/report/alert independently of price or direction. |
| S06 | Market then related news; news then compatible market; broad-market move | Apply existing correlation rules; compatible signals share an event where those rules support it; SPY context does not imply known causation. |
| S07 | Duplicate id/URL/revision, irrelevant/old/missing-source news; comparison-only SPY | No repeated classification/research/delivery, no rejected-news cooldown, and explicit SPY watchlist distinction. |
| S08 | Startup/reconnect handoff, stale/interrupted stream, missed bars, closed-hours/weekend restart | Recover valid regular minutes/latest completed daily without late-firing historical fast/gap alerts; pending work/news does not depend on socket connection. |
| S09 | Regular close during research or delivery; bounded backlog | Socket closes appropriately; post-work REST recovery, polling, and fair pending work continue; no per-pass notification storm. |
| S10 | Alpaca/SEC/OpenAI errors, throttling, malformed input and missing keys | Existing retry bounds/pacing hold; no fabricated signals/reports, failures are visible, unrelated saved reports remain deliverable. |
| S11 | Invalid report/citation, conflicting evidence, unknown cause, missing sector/filing context | Schema/source validation is enforced; valid sparse reports state gaps; invalid results create no notification but still account for calls. |
| S12 | Research interruption/retry, stale update during work, count or USD exhaustion | No false successful report, duplicate charge, or restarted-budget bypass; latest update and fair retry policy remain correct. |
| S13 | Discord definite rejection, long 429, 5xx, response loss, oversized/malformed success | One uncertain resend after 15 minutes, delayed by longer provider waits; any nonsuccess then holds for review, without another retry chain or silent console fallback. Other work progresses. |
| S14 | Crash before send, after remote acceptance, after receipt, after completion, and during the uncertain resend | Durable-claim/receipt matrix from spec 010; known success not resent, 15-minute deadline and single resend allowance survive restart, possible duplicate messages share a logical id, and explicit recovery works after automatic recovery stops. |
| S15 | Webhook/key/cap changes, UTC rollover, v5 upgrade, two owners | No replay of completed console/Discord output; no migration/restart spend reset; ownership enforced and limitations visible. |
| S16 | Untrusted article/tool text, malformed URLs, secrets in provider exceptions, mentions | Treat content as evidence, not tool/eligibility instructions; no expanded tools, leaked secrets, or Discord pings. |
| S17 | Quiet operation, heartbeat/watch independently enabled, shutdown/restart | Status distinguishes no trigger, deferred work, unavailable provider, and delivery hold; clean stop preserves durable recovery state. |

S06 must not invent a universal correlation promise. In particular, unclear-direction
news and multiple compatible episodes may remain separate under current rules.
Document which cases merge and why; a desired broader merge is a new event-policy
decision, not a test adjustment that silently changes the product.

## Live verification, separate from replay

Deterministic replay proves control flow and recovery. It does not prove real account
entitlements, current API payloads, citation usefulness, provider latency, or visible
Discord rendering. Complete a bounded manual trial after the automated suite passes.
No live tests enter pytest or CI.

Before running, record the chosen explicit watchlist (2–5 symbols plus comparison
SPY), feed, model/prompt/price versions, isolated database, intended Discord test
channel, available keys/SEC identity, and fixed duration/call/spend/send ceilings.
Do not copy secret values into evidence. Use configured caps and stop when any trial
ceiling is reached. An operator must explicitly authorize external test messages and
paid calls; this spec review does not authorize those side effects.

Minimum evidence:

1. Real provider retrieval and live-loop monitoring for at least 60 continuous
   minutes of a normal regular session, plus an observed regular-close transition
   (which may occur in a separate bounded trial). Record missing/stale data,
   reconnect/backfill behavior, polling cadence, and clean stop/restart.
2. One real news-classifier response and one complete real research run using
   observed source metadata, including a hosted search and SEC lookup when access
   permits. Record unavailable sources honestly. If the fixture/live event never
   requests a tool, use a separate bounded adapter check to verify that integration.
3. A real saved-report → Discord receipt → notified path with visual inspection
   of the concise alert, source links, uncertainty, no mentions, and visible label
   on synthetic events. Restart the same database and verify no second POST for
   known success. Provider success alone does not prove report usefulness.
4. When a real market trigger does not occur, use an explicitly labeled synthetic
   input through the normal normalized-input/event boundary, in the isolated trial
   database/channel, to exercise research/delivery. Record it as synthetic; do not
   lower production thresholds or claim a natural market event was observed.
5. Inspect report evidence and alert usefulness for at least market-only, news-only,
   and combined cases. Deterministic cases may cover categories absent from the live
   trial. Distinguish evidence from inference, verify saved cited sources support
   factual claims, and retain allowed review posture without trade instructions.

Do not deliberately overload providers or manufacture real rate-limit failures.
Fault injection proves those paths offline. Provider denial/unavailable credentials
or inability to run the trial leaves live verification pending with a concrete
follow-up; it does not silently turn the MVP complete. Earlier market trials can
inform the record but do not replace verification of the final integrated version.

## Evidence, fixes, and limits

Create `VALIDATION.md` in this directory during execution, containing date, revision,
Python/OS, commands/results, scenario-to-test mapping, counts, observed recovery and
latency, cost estimates versus provider usage where available, and unresolved issues.
Never describe planned tests as passed. Record numerical observations without
promising a production SLA from a short trial.

For every failure, name the affected product criterion, reproduce it deterministically
where possible, add the regression with the smallest fix, and rerun affected/full
checks. Threshold tuning needs observed noisy/missed behavior, before/after replay,
and an owning-spec update; do not tune just to make a demonstration fire.

The final limitations record must address: local downtime, synchronous-loop latency,
IEX/SIP entitlements/coverage, news lookback/recency and outages, provider revision
and search behavior, missing evidence, conservative budget holds/unknown usage,
possible duplicate alerts from the single automatic uncertain resend and manual
recovery after it is exhausted, data growth/backup, and fixed-session handling
of holidays/early closes. Test nonstandard-session inputs deterministically. Do not
claim a full exchange calendar or guaranteed recovery of all offline news.

Provide a practical local runbook in README: configure/start, recognize offline vs
live/console vs Discord, check liveness/backlog/budget, rotate configuration, recover
held delivery, stop cleanly, and back up/restore SQLite with the process stopped.
No deletion of live history, automated retention policy, or backup service is required.

## Product success mapping

| Product criteria | Evidence required |
| --- | --- |
| 1–3: monitoring, recovery, deterministic market rules | S01–04, S08–09; normal-session and close live observations |
| 4: cheap, direction-independent news triage | S05, S07, S10; real classifier check |
| 5: independent signals/shared events | S01, S05–06; documented correlation limitations |
| 6–7: bounded research, valid cited reports | S10–12, S16; real research/tool checks and report review |
| 8: useful Discord alerts without routine duplicates | S04, S13–15; real receipt/rendering and restart evidence |
| 9–10: durable history and replay through live core | S08–09, S12–15, S17; file-backed restart/replay evidence |
| 11: replaceable external boundaries | Same core exercised by real adapters and fakes; no provider payload types in core logic |
| 12: count/cost limits and provider backoff | S09–10, S12–15; persisted usage/reservations and observed trial limits |

## Acceptance criteria

- [ ] AC-01: S01–17 have named deterministic evidence through live boundaries,
  including both movement directions and both news directions. Settled replay is quiet.
- [ ] AC-02: All required crash/restart, provider/research/delivery-failure, migration,
  rollover, stale-result, and budget paths retain truthful history and recover or
  explicitly hold work; no silent loss masked as success. Prove that the uncertain
  resend waits at least 15 minutes and happens at most once across restarts; record
  the accepted duplicate risk separately from unintended repeated sends.
- [ ] AC-03: The bounded live trial verifies final integrated monitoring,
  classifier/research/tools, real Discord delivery, and restart without known-success
  duplicates. Synthetic input and provider/access limits are labeled.
- [ ] AC-04: Saved reports and actual alerts pass the stated usefulness/citation/
  uncertainty review; evidence gaps do not become invented certainty or trade advice.
- [ ] AC-05: The runbook and limitations let an operator start, inspect, recover,
  back up, and stop the app. Secrets stay out of fixtures/logs/evidence.
- [ ] AC-06: Every product success criterion has a linked result in VALIDATION.md;
  failures have regressions/fixes or clearly justified limits that do not waive core
  criteria. No unresolved duplicate-send, secret-leak, data-loss, or count-limit bug.
- [ ] AC-07: Ruff format, Ruff lint, mypy, and full pytest pass on the final revision;
  required CI passes. Product, architecture, decisions, specs, and roadmap agree on
  implemented behavior, measured readiness, and deferred scope.

Milestone 8 and the local MVP are complete only when AC-01–07 pass. Automated-only
completion may be reported as such, but does not close the live-verification gate.
