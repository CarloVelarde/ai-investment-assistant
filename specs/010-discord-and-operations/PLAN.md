# Implementation Plan: Discord and Operations

**Document status:** Refined draft

**Implementation status:** Not started

Implement [SPEC.md](SPEC.md) in the slices below. Track evidence in
[TASKS.md](TASKS.md). This plan targets current SQLite version 5; the next schema
version is 6 unless an intervening migration lands first. No runtime code changes
are part of this documentation review.

## Existing seams and gaps

| Current code | Change needed |
| --- | --- |
| `models.NotificationAttempt` and `storage.save_notification_result` | Add pre-send claims, durable receipts, destination, outcomes, and scheduled retries; current Boolean success cannot express unknown delivery. |
| `EventManager._notify` / `_notification_ready` | Resume receipts, distinguish retryable/permanent/uncertain delivery, keep current-update guards; do not requeue research for a delivery failure. |
| `EventManager.process_pending` / `_research` | Bound external sends in live mode, defer newly researched reports to the next pass, preserve offline draining and research fairness. |
| `reporting.emit_console_notification` | Emit after durable completion; acknowledge the nontransactional log gap. |
| `config.Settings` / `main.main` | Secret webhook and bounded settings; explicit notifier selection without breaking fake injection; local ownership and narrow recovery-command routing. |
| `storage.reserve_research_attempt` | Atomically reserve configured starts and shared estimated USD, preserving retry and current-update checks. |
| `news_ingest._classify_pending` | Move durable count reservation before I/O; apply configured pass/day and shared spend limits after filters. |
| `OpenAINewsClassifier` / `OpenAIResearchModel` | Expose validated internal usage metadata even when semantic output fails; cap classifier output; no provider payloads in core or secrets in errors. |
| `ResearchRunner` | Persist per-request start/settlement and tool-fee accounting, including stale/invalid reports and interruptions; retain existing tools and call ceilings. |
| `main._process_pending_and_recover` | Recover minutes after external delivery attempts as well as research, including timeout and regular-close transitions. |
| `ops_log.emit_heartbeat` / watch logger | Add backlog age, held/failed state, destination, and per-kind budget admission status. |
| `tests/test_milestone_scope.py` | Replace the obsolete blanket ban on `discord` with boundary-specific guards; retain no-bot/no-worker/no-trading rules. |

Add `discord_notify.py` for HTTP/URL/embedding and a small budget module only if
shared accounting warrants it. Recovery commands may have a small dedicated module.
Do not turn the event manager into a Discord client or introduce a service hierarchy.
The notifier boundary returns internal outcome/receipt data; console and fake
notifiers need an explicit compatibility path. Keep HTTP and time injectable.

## Slice 1 — Persistence and ownership

Add logical delivery state keyed by event/update and append-only submission history.
Prefer a small `notification_deliveries` table plus the extended existing attempts
rather than overloading one row with both logical delivery and HTTP attempt identity.
The logical row owns current state/destination/retry authorization, first uncertainty
time, persisted 15-minute resend deadline, and consumed uncertain-resend allowance;
attempts own immutable submission outcomes and receipts. Keep a unique successful completion per
update and one active claim. Validate receipt/state combinations at write boundaries.

Store destination fingerprint, destination/global wait, and disabled reason in
small SQLite operational state. A process-lifetime OS lock tied to the canonical
database path prevents two live owners and racing recovery mutations. It must work
on declared macOS/Linux hosts and disappear on crash without a manual lock-file
cleanup. Do not treat an expiring timestamp as proof that an old process stopped.

Migration is atomic and rerunnable through the existing initializer:

- Preserve signals/events/reports/research history and all old notification rows.
- Existing notification success becomes completed `console` delivery, with no
  invented Discord message id. Preserve its timestamp; never replay it externally.
- Legacy failed console attempts keep their original outcome/history; a current
  saved report may enter the new live delivery path without being treated as an
  unknown Discord send. Old attempts do not consume new Discord retry allowance.
- Backfill logical records only as needed; no external calls during migration.
- Preserve classifier daily counts and research starts. If the migration day has
  any legacy model calls, mark pre-migration USD use unknown and defer new model
  calls until the next UTC day. Status explains the one-day hold; delivery remains
  available. Do not invent exact historical costs or reset counts to zero.
- Fresh databases and databases with no current-day legacy calls can reserve
  immediately. Changing settings does not bypass a migration-day hold.

Use a reservation ledger plus per-request entries with stable identities, UTC
reservation day, owner (research attempt or article/ticker attempt), model/policy
version, reserved/charged microdollars, request state, usage, and search fees.
Admission/count reservation is one SQLite transaction. Settlements are idempotent.
Do not couple payment accounting success to valid report/classification success.

## Slice 2 — Adapter and payload

Implement pure URL validation and embed construction, then bounded HTTP. Return
internal receipt, definite rejection, permanent rejection, or unknown outcome; do
not infer whether bytes were sent from an arbitrary exception string. Inject the
transport to prove pre-send failures; default unknown when proof is unavailable.

The transport does one submission, caps total duration and read size, refuses
redirects, parses provider waits safely, and strips unsafe error details. No hidden
retries underneath the durable attempt scheduler. Validate response ids and source
links, disable mentions, and truncate rendered content within all field/total bounds.

## Slice 3 — Delivery lifecycle and recovery

1. Under the local owner lock, select one current due delivery fairly. Skip held,
   disabled, or waiting destinations without blocking unrelated work.
2. Recheck report/current update, receipt, destination, and waits. Atomically consume
   a manual authorization, claim an ordinary attempt, or consume the one due
   uncertain-resend allowance with its claim. Refused write means no HTTP call.
3. Send outside the transaction. Record the attempt result and any destination wait.
4. Save a returned receipt even if the event became stale. Complete the matching
   logical delivery; mark the event only if that update is still current.
5. On restart, finish acknowledged rows without HTTP and move unresolved claims
   to uncertain. The first recorded uncertainty starts a durable 15-minute wait;
   restarts preserve it. An already consumed uncertain-resend allowance stays spent,
   even if the process crashed before POST. Never reopen completed delivery to
   repair missing console output.
6. Definite failures get persisted 1/2/4/8-minute backoff within five ordinary
   submissions. A first uncertain outcome ends that retry path and permits one
   additional resend after 15 minutes, with the same logical footer id. This makes
   six automatic submissions the absolute maximum. Any nonsuccess of that resend
   holds the delivery for operator recovery; it cannot start another retry chain.
   Provider waits may delay it further; supersession or destination changes prevent
   it. Permanent failures stop. New material updates stay independent.

Implement `notifications list`, `retry`, and `confirm` as specified, dispatched
before live startup/backfill. Read-only listing must work without provider keys.
Mutating commands acquire exclusive ownership. Receipt lookup must verify the
logical id and use the currently matching destination; a changed/removed credential
cannot confirm an old destination without restoring that configuration. Explicit
retry to a changed destination retains prior uncertainty/history and duplicate risk.
Reject manual retry while the automatic uncertain resend is pending; confirming a
receipt cancels that pending resend. Manual authorization never restores the spent
automatic allowance. A command does not silently clear a destination/global wait.
Record only safe audit
fields and authorization time, not arbitrary operator input.

## Slice 4 — Durable model-use admission

Preserve event eligibility and ordinary filters. Reserve a whole research run's
$1.62 allowance alongside its start, or a classifier call's $0.0825 alongside its
count. Use the policy in SPEC; do not duplicate price constants in multiple callers.
A missing key, count limit, migration hold, unknown price, or insufficient estimated
budget yields an explicit deferral without I/O/start consumption.

Track each request before invoking a provider. The research allowance is allocated
across at most five requests and three searches; a request records its granted
search slots before I/O. Successful usage releases only unused allocation. An
uncertain request consumes its allocated token and search allowance. Durable proof
that later requests were not started lets the run release those unused portions.
An unclosed run at restart settles conservatively from those records, without
losing calls or spending the same reservation twice.

Parse usage independently before report/classifier semantic validation. Request
counts include failures. Missing/invalid counters keep the relevant full allowance;
valid over-allowance usage records the real estimate and blocks further model work
for that day. Invalid output must not accidentally refund the provider call.
Known reported input includes search content; if required usage cannot be established,
retain the allowance instead of estimating search tokens as zero.

Use integer arithmetic with upward rounding, not floats. Preserve prior-day charges
for a run crossing midnight. New starts use the new UTC day. Check per-kind admission
separately so classification can fit when a full research reservation cannot.

## Slice 5 — Live wiring and operations

Keep a maximum of one external send before research per live pass. Deliveries created
by that pass's research become due next pass. Return enough internal work information
for the loop to recover regular minutes after either external activity. Avoid changing
promotion rules to enforce these scheduling limits.

Extend existing status/loggers, and document settings plus exact recovery commands
in README / `.env.example` when implemented. Console mode must be explicit, including
how adding Discord affects already completed reports. Describe conservative budget
reservations, migration-day hold, and unknown-delivery tradeoffs plainly.

## Verification and handoff

Use existing in-memory/fake providers and file-backed temporary SQLite for restart
and migration tests. Inject transport faults at every external/local boundary and
assert persisted state, POST counts, counts/spend, and current event/update. Include
v5 migration and older supported migration chains; do not use private user databases.

Run all four repository checks. Update scope tests to allow only the intended new
boundaries; do not remove architectural assertions to make Discord pass. Link each
acceptance criterion to named tests when completed. No live calls in pytest.

Milestone 7 completion hands working delivery, budget, recovery commands, and operator
instructions to [spec 011](../011-full-loop-hardening/PLAN.md). It does not claim
full-loop live reliability, report usefulness, or tuned detection quality.
