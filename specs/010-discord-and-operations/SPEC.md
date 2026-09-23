# Feature Specification: Discord and Operations

**Document status:** Refined draft — ready for implementation review

**Milestone:** 7 — Discord and operations

**Implementation status:** Not started

Behavior belongs here; [PLAN.md](PLAN.md) owns implementation and
[TASKS.md](TASKS.md) owns execution. Durable decisions: D-030–D-032 in
[DECISIONS.md](../../docs/DECISIONS.md). Milestone 8 has its own
[spec 011](../011-full-loop-hardening/SPEC.md); completing this spec does not
complete the MVP verification gate.

## Purpose and existing foundation

Deliver saved reports to one Discord channel, recover delivery failures without
repeating research, and bound model use across restarts. Keep one local process,
SQLite, the existing market/news boundaries, and the event manager's eligibility
rules. Neither the notifier nor the budget ledger decides significance.

Today, `EventManager._notify` calls a console notifier before saving its result.
SQLite v5 prevents multiple locally recorded successes but has no pre-send claim,
receipt, retry schedule, destination, or uncertain-outcome state. Research already
reserves starts before I/O (20/day); classification counts calls after I/O
(100/day, 20/pass). There is no shared USD ledger and no six-hour cooldown timer.
This spec closes those gaps; it does not claim they are already implemented.

## Scope

In scope: one incoming webhook, durable delivery state, bounded scheduling,
operator recovery commands, lowerable model-call caps, shared estimated spend,
and extensions to existing logs/status. Use stdlib HTTP and injected clocks,
transport, and SQLite in deterministic tests.

Out of scope: bot/gateway, threads/forum channels, files, slash commands, workers,
new services, detector thresholds, correlation or episode changes, news significance
or research-prompt changes, model upgrades, new research tools, portfolio features,
and trading. Full-loop live trials belong to Milestone 8. Do not invoke real
providers while implementing/testing this slice without a separately scoped trial.

## Delivery contract

### Destination and eligibility

- Offline fixtures use console only, even if a webhook is configured. Live mode
  uses Discord when configured, otherwise console. Mark destination explicitly.
- Require a persisted report matching the current event/update before claiming.
  Skip superseded reports. Do not synthesize a report or rerun research for delivery.
- A blank webhook is a supported console fallback. Log its absence once when a
  live report is ready. A configured but failing webhook never silently falls back.
- A saved report can deliver without an OpenAI key or available model budget.
- Successful console deliveries stay complete after adding a webhook. Changing
  webhook URLs also does not replay completed updates. There is no back-catalog send.
- Legacy fake reports remain clearly labeled `FAKE RESEARCH — NOT INVESTMENT
  ANALYSIS` if delivered; live failures never create fake reports.

### Identity and durable states

One stable, short logical `delivery_id` identifies an event/update across retries
and appears in the alert footer. Each HTTP submission has a separate attempt id.
Store destination kind and a non-reversible fingerprint of the normalized webhook
URL, never the URL/token. Attempts retain claim time, completion time, outcome,
message id when known, safe reason, attempt number, and retry time. The logical
delivery also retains `uncertain_since`, `resend_not_before`, and
`uncertain_resend_used` across all attempts and restarts.

| State | Meaning and next action |
| --- | --- |
| `CLAIMED` | Committed before POST. On process restart, absence of a receipt means `UNCERTAIN`, even if the crash may have preceded the POST. |
| `ACKNOWLEDGED` | Valid message id durably saved. Finish locally without another POST. |
| `SUCCEEDED` | Delivery completion committed. Never resend automatically. |
| `RETRY_WAIT` | Definitely not delivered; retry only when due and below the attempt limit. |
| `PERMANENT_FAILURE` | No automatic attempts; operator intervention required. |
| `UNCERTAIN` | May already exist in Discord; one automatic resend after 15 minutes if unused and eligible, otherwise hold for operator recovery. |
| `SUPERSEDED` | No longer current; no new POST. Retain prior attempt outcomes/receipts. |

Commit the claim before HTTP; do not hold a SQLite transaction across network I/O.
On success, persist the receipt first, then atomically complete the delivery and
conditionally mark the matching current event notified. `last_notified_at` uses
the durable completion time, not request start. A crash after receipt persistence
requires only the final local write. A crash after remote acceptance but before
receipt persistence is uncertain; returning an id in memory is not durable proof.

Keep a receipt even if the event changes during the request. Do not mark the newer
update notified, discard the receipt, or send the old report again. One open claim
and one successful completion per event/update are enforced by storage. Only one
live application may own a database; reject a second live owner before external
calls, with a process-lifetime local lock that is released on exit/crash.

### HTTP outcomes and retries

Use `POST ...?wait=true`. Success requires a 2xx response and a valid nonempty
numeric Discord message id. Classify conservatively:

| Outcome | Action |
| --- | --- |
| Proven pre-send DNS/connect failure | Retryable definite failure. |
| 429 | Definite rejection; store the provider wait and defer the whole destination. |
| 400 / other definitive 4xx | Permanent for this payload; no tight loop. |
| 401 / 403 / 404 | Disable this destination fingerprint across all reports until operator recovery or a changed webhook. |
| Timeout, dropped response, unclassified transport exception | Uncertain unless the transport proves no request was sent. |
| 5xx | Uncertain: do not assume the server failed before storing the message. |
| 2xx with missing/invalid id, malformed/oversized body, or unexpected 204 | Uncertain; missing local proof is not proof of remote failure. |
| Redirect | Do not follow; permanent configuration/protocol failure. |

The ordinary definite-failure path allows five submissions: the first plus four
retries. Definite failures 1–4 wait 1, 2, 4, and 8 minutes from completion. Definite
failure 5 stops. The first uncertain outcome ends this ordinary retry path and
permits exactly one additional automatic resend under the rules below, even if
uncertainty occurred on submission 5. Thus at most six automatic submissions are
possible, and at most one follows an uncertain outcome. There is no unused
16-minute retry. Store waits; never sleep in the live loop.

For 429, parse finite nonnegative seconds from `Retry-After` and/or JSON
`retry_after`, using the larger valid value and no less than normal backoff.
Never shorten a provider wait to an application cap. An unrepresentable value
holds the destination for intervention; a missing/invalid value uses normal
backoff. Also honor exhausted-bucket reset headers on successful responses.
Destination-wide waits survive restart and apply to other reports and manual
retries. Changing a webhook must not bypass a recorded global rate-limit wait.

### Unknown outcomes and operator recovery

On the first uncertain outcome, persist `uncertain_since` when the application
first records that outcome and set `resend_not_before` to 15 minutes later. For an
unresolved claim discovered on restart, start the wait when recovery first marks
it uncertain. Later restarts preserve that deadline rather than restarting the wait.

When due, send once more only if the report is still current, no durable receipt
exists, the same destination is configured and enabled, and all provider/global
waits have expired. Atomically set `uncertain_resend_used=true` with the new claim
before HTTP. Use the same logical delivery id/footer and saved report, but a new
attempt id. This allowance belongs to the delivery, not the process or destination.

A successful resend saves its receipt and completes normally. Any other outcome
(including a definite failure or another uncertain response) ends automatic work;
preserve the original uncertainty and latest attempt outcome for operator review.
Do not restart the ordinary retry chain. A crash after consuming the allowance but
before saving a receipt also stops automatic work, even if the POST never happened.
Persist any new destination/global hold from the resend for other work as usual.

The resend may create a duplicate if Discord accepted the original request. It is
not proof that the first message was absent. The user selected this bounded resend
policy to favor unattended recovery. Both messages carry the same logical delivery
id. Receipt lookup and manual recovery remain available when automatic recovery
cannot finish; confirmation of a receipt cancels a pending resend.

Provide narrow local commands through the existing executable:

- `notifications list`: show current and historical held/failed deliveries, ids,
  destination kind, timestamps, safe reason, retry time, and known receipt. Read-only;
  no provider calls, credentials, or report bodies in default output.
- `notifications retry --delivery-id ID --accept-duplicate-risk`: explicitly
  authorize one additional submission of a held/permanent current delivery. Persist
  that authorization before I/O; ordinary live processing performs it once when
  provider waits permit. Reject this command while the automatic uncertain resend
  is pending; it must not bypass the 15-minute wait or add a parallel authorization.
  No automatic retry chain follows that extra submission, and it never replenishes
  the automatic uncertain-resend allowance.
- `notifications confirm --delivery-id ID --message-id ID`: after the user finds
  the alert in Discord, fetch that message with the configured webhook and verify
  its logical delivery id before saving the receipt/completing locally. Failure or
  mismatch changes no success state; it never POSTs a message.

Mutating recovery commands require the live loop stopped and the same local lock.
Commands are idempotent: an unused retry authorization is not multiplied by
repeating the command; success cannot be reopened; stale updates cannot be resent.
An explicit retry may probe a disabled destination once for that delivery; it does
not re-enable the destination for other reports unless the probe succeeds.
Rate limits and safe-error handling also apply to receipt lookup. A receipt for a
superseded update may be retained without completing the newer event.

A changed destination may release destination-configuration failures for the
current unsent report, retaining history. It must not clear uncertain outcomes,
reset already consumed automatic attempts, or release a global rate-limit hold.
Changing or removing the webhook holds an uncertain delivery instead of resending
to a different destination or turning it into console success. Restoring the same
webhook may resume its unused, due resend; otherwise resolve it explicitly. No direct SQL edits are required for recovery.

### Console and suppression

Commit console completion before emitting its summary. Print once on the ordinary
path; a crash between commit and output may lose that console line. Do not promise
exactly-once console output or resend externally to repair a missing log line.
Discord summaries likewise print after durable completion as best-effort logs.

Preserve current event policy: exact duplicates are ignored, routine same-level
repeats remain quiet even after six hours, and material updates are immediately
eligible. A new episode is independent. Failures, claims, deferrals, and rejected
news do not establish successful notification history. No new timer-based cooldown
is added; a timer would suppress no additional class of update under these rules.

## Alert and transport

One embed, no message content; explicitly set `allowed_mentions: {"parse": []}`.
Sanitize mention/control syntax in all untrusted text. Keep the saved report intact.

- Title: ticker + posture (or `fixture report`), at most 256 characters.
- Description: saved summary, at most 500 characters, ellipsis on truncation.
- Up to three fields: uncertainty; sources; event/update and trigger/report times.
  Each name is at most 256 and each value at most 1,024 characters.
- Sources: at most three saved sources in one field, using validated HTTP(S) links
  when present, otherwise readable source titles/references. Do not invent URLs.
  Keep each included link whole; omit excess sources with an omitted-count marker.
- Footer: review-only wording and logical delivery id, at most 200 characters.
- Total embed text at most 5,000 characters. Stable ordering and Unicode-safe
  truncation; no raw model output, private notes, tokens, or credentials.

`INVESTMENT_ASSISTANT_DISCORD_WEBHOOK_URL` is an optional secret. In live mode,
nonblank values must be HTTPS on `discord.com`, `discordapp.com`,
`canary.discord.com`, or `ptb.discord.com`, with `/api/webhooks/{numeric_id}/{token}`
or `/api/v10/webhooks/{numeric_id}/{token}`. Accept only the default HTTPS port,
no userinfo/query/fragment/extra path, and no encoded path separators. The adapter
adds `wait=true`. Validate without echoing the rejected input, including Pydantic
validation output. Blank means console; whitespace is stripped.

Use a 10-second total request deadline, bounded response reads (64 KiB), no
redirects, and no hidden transport retries. Safe errors contain only application
reason codes, HTTP status, or exception type, at most 300 characters. Do not persist
or log response bodies, request headers, or webhook URLs.

## Scheduling and operational status

Before research, process at most one due external delivery per live pass, selected
by oldest eligibility time with a stable id tie-break. Resume known receipts locally
without a POST. A report created by research waits for the next pass in live mode;
offline fixture callers can keep their current drain behavior. Held/waiting work
must not starve other deliveries or research. Research remains at most one run/pass.

A delivery call can block market ingestion. Extend post-work minute recovery to
runs that performed external delivery as well as research; session transitions,
news polling, and socket recovery must still progress with a delivery backlog.
Include failure/timeout paths in that recovery test.

Keep quiet JSON INFO and independent heartbeat/watch opt-ins. Log transitions once,
not idle repeats: destination unavailable, claim, receipt/success, retry scheduled,
uncertain outcome, resend scheduled/claimed/exhausted, permanent failure, operator
resolution, or budget deferral. Include
applicable ticker/event/update/delivery ids, never the webhook or report body.

Add heartbeat fields: `discord_configured`, `notification_destination`,
`pending_notifications` (current deliverable updates without success, including
held ones), `uncertain_deliveries`, `failed_deliveries`, `oldest_pending_age_seconds`,
and model estimated charged/reserved/remaining USD. Notification listing/watch
transitions distinguish uncertain work awaiting its one resend from uncertainty
requiring operator review. Report separate research and
classifier deferral reasons; insufficient funds for research need not block a
smaller classification. Historical superseded attempts do not inflate current
backlog. Watch logging narrates the same transitions only when enabled.

## Model rate and estimated-cost controls

### Settings and count reservations

All names use `INVESTMENT_ASSISTANT_` below. Call settings are integers; USD is a
finite decimal with at most two fractional digits. Reject negative, NaN/infinite,
and over-maximum values without printing secret settings. Zero disables new calls.

| Suffix | Default | Maximum |
| --- | --- | --- |
| `RESEARCH_STARTS_PER_DAY` | 20 | 20 |
| `CLASSIFIER_CALLS_PER_DAY` | 100 | 100 |
| `CLASSIFIER_CALLS_PER_PASS` | 20 | 20 |
| `DAILY_MODEL_BUDGET_USD` | 2.00 | 10.00 |

Atomically reserve research start + estimated allowance before the first model
request; classifier count + allowance before each article/ticker request. Preserve
filters before reservations. A blocked call consumes neither count nor spend.
Failed/unknown/interrupted calls consume their reserved counts. Classifier results
and usage are separate: invalid classification can still incur a charge.

Durable counts and reservations use UTC. Restart/settings changes cannot erase
usage; lowering a cap below usage blocks further starts. A research run belongs to
its reservation day even if it crosses midnight; continuation uses that reservation,
not a fresh day's allowance. New attempts use the new day. Existing research retry
fairness/backoff and news recency/filter policy remain; expired articles are not
revived just because a budget resets.

### Pricing and reservation policy

Pinned policy uses standard, undiscounted USD pricing per million tokens:

| Model used by existing code | Input | Output |
| --- | --- | --- |
| `gpt-5.4-mini-2026-03-17` | 0.75 | 4.50 |
| `gpt-5.4-nano-2026-03-17` | 0.20 | 1.25 |

Official [mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini) and
[nano](https://developers.openai.com/api/docs/models/gpt-5.4-nano) pages checked
22 September 2026. Hosted `web_search` adds $0.01/call and search-content input
charges under [OpenAI pricing](https://developers.openai.com/api/docs/pricing).
Store policy version/date with every reservation; no pricing fetch at runtime.

Do not call the old 8,000/25,000 input-token estimates “worst case.” Local request
size does not bound provider-added search context. For this first implementation,
reserve conservatively using the models' documented 400,000-token context size
as the input allowance per request, without discounts:

- Classifier: 400,000 input + 2,000 output = $0.082500 per call. Add an actual
  `max_output_tokens=2000` request limit (currently missing), without changing the
  classifier's significance policy. Preserve bounded article content.
- Research: five requests × (400,000 input + 4,000 output), plus three search fees
  = $1.620000 per run. Preserve existing five-request / three-search ceilings and
  4,000 output-token request limits. Reserve the whole run before consuming a start.

These deliberately conservative reservations may defer a run whose actual cost
would have been small. The $2 default permits a first full research reservation;
a setting below $1.62 permits no new research until changed. Status must explain
required reservation versus remaining funds, rather than saying the budget is zero.

Admission requires `charged + outstanding reservations + new reservation <= cap`.
Use integer microdollars, rounding charges upward. Track each request durably before
I/O. Settle with validated usage even for invalid/stale reports. Input counts include
search content when supplied in usage; add search fees separately and never count
the same input twice. Missing/malformed usage or tool counts retain the relevant
full allowance. Unknown requests retain it across restart; a crash cannot make
spend disappear. Release unused requests/search allowances only when durable state
proves they were never submitted; settle each request once.

On upgrade from v5, preserve all existing counts and successes. If any legacy model
call occurred that UTC day, its USD cost is unknown: hold new model calls until the
next UTC day, while continuing delivery. A fresh database or day with no legacy
calls can start immediately. Expose this one-day migration hold; do not fabricate
historical charges. The plan owns the schema details.

If observed usage exceeds an allowance, record the higher charge, expose an
estimation-overrun reason, and stop new model work for that UTC day. Never clip
usage to make the budget look satisfied. Unknown models/pricing fail closed for
new calls. This controls application estimates, not exact invoices, account-wide
spending, taxes, or future provider prices. There is no absolute billing guarantee.

Alpaca/SEC keep existing bounded request/retry/pacing rules; do not claim a new
configurable quota for them. Saved-report delivery ignores model admission limits.

## Acceptance criteria

- [ ] AC-01: Discord success requires a saved current report, pre-I/O claim,
  receipt, and guarded completion; repeat passes/restarts make no second POST.
- [ ] AC-02: Crash tests cover before POST, after remote acceptance before receipt,
  after receipt before completion, and after completion before console output.
  Unknown outcomes wait 15 minutes for one automatic resend; known receipts finish
  locally; stale receipts survive. The wait and consumed allowance survive restart.
- [ ] AC-03: The five-submission ordinary bound, 1/2/4/8-minute waits, and single
  additional uncertain resend persist across restarts (at most six automatic
  submissions). Test just before/at the 15-minute boundary, a longer provider wait,
  uncertainty on submission 5, repeated uncertainty, definite resend failure, and a
  crash after consuming the allowance. No failure of that resend restarts a retry
  chain. Known success, supersession, or a changed destination prevents resend.
  429/bucket waits apply across reports and are never shortened; permanent errors
  disable only the correct scope. Malformed 2xx and 5xx follow the uncertain policy.
- [ ] AC-04: List, explicit single retry, and receipt-confirmation commands recover
  held work without SQL edits/research. Repeated commands, destination changes,
  global waits, stale updates, and second-process ownership are covered.
- [ ] AC-05: Blank webhook/offline mode make no Discord calls. Configured failures
  never fall back. Destination changes do not replay success. Fake labels survive.
- [ ] AC-06: Routine repeats remain quiet; material updates/new episodes remain
  eligible; failures/rejected news never set successful notification time. No
  cooldown timer or promotion change is introduced.
- [ ] AC-07: Alert links, mention suppression, truncation, total/field limits,
  URL validation, total deadline, bounded body, redirects, and secret-safe errors
  are tested at the adapter boundary.
- [ ] AC-08: Count and spend reservations precede calls, survive crashes/settings
  changes/UTC rollover, and cover failures, missing usage, invalid/stale results,
  hosted search, overrun, and settlement replay. Zero/invalid caps are tested.
- [ ] AC-09: Insufficient budget consumes no start and leaves no fake report;
  saved reports still deliver. Missing key and separate per-kind deferrals stay
  understandable and do not repeat on every pass.
- [ ] AC-10: One due external send/pass, notification-first ordering, research
  fairness, post-delivery recovery, session close, and backlog progress are tested.
- [ ] AC-11: SQLite v5 upgrade preserves history/successes and counts; older supported
  migrations still work. Migration-day unknown spend is handled conservatively.
  Status/logs reflect actual state with independent heartbeat/watch switches.
- [ ] AC-12: All repository checks pass without live services; scope guards permit
  only the new adapter/operations paths. README and `.env.example` describe working
  behavior, limits, and recovery. Milestone 7 completes only after AC-01–11 pass;
  Milestone 8 remains open.

## Review conclusions

Keep the original draft's webhook, saved-report boundary, one process, bounded
calls, quiet operations, and independent market/news eligibility. Replace absolute
exactly-once claims, truncated provider waits, the inconsistent fifth-retry schedule,
and post-response-only spend tracking. Retain the user's selected one-time resend
after 15 minutes, explicitly accepting possible duplicates; it is not a lookup or
an exactly-once guarantee.
Remove the six-hour timer because it has no defined effect under existing policy.
The conservative cost reservation is an explicit first-release tradeoff to measure
in Milestone 8, not an assertion that each report normally costs $1.62.

Webhook behavior follows [Discord's webhook reference](https://docs.discord.com/developers/resources/webhook)
and [rate-limit guidance](https://docs.discord.com/developers/topics/rate-limits),
checked 22 September 2026. Conservative treatment of ambiguous outcomes is the
application's policy; it is not a claimed Discord exactly-once guarantee.
