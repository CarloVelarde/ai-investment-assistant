# Feature Specification: Research and Reporting

**Document status:** Complete

**Milestone:** 6 — Research and reporting

**Implementation status:** Complete

Implementation approach: [PLAN.md](PLAN.md). Execution and acceptance coverage:
[TASKS.md](TASKS.md). This feature implements the existing product and architecture
decisions. Deterministic tests cover the local evidence packet, SEC and OpenAI
adapters, the bounded runner, live-loop scheduling, post-research recovery, console
rendering, and representative news-only, combined, and broad-market reports.
No opt-in live smoke against Alpaca, OpenAI, or SEC was run; that check stays
outside pytest. Discord remains Milestone 7.

## Purpose

Replace fake research with one bounded, application-controlled investigation
for each event that already needs work. The event manager still decides
eligibility. Research explains what happened, cites sources, states
uncertainty, and returns a cautious posture. It does not decide whether the
event exists, whether to notify, or whether to buy or sell.

Market-only cases may honestly conclude that the cause is unknown. Discord
stays a later milestone; a saved report still uses the existing console
notifier.

## Scope

### In scope

- Assemble a local evidence packet from saved event, signal, market, news, and
  prior-report state after the event manager marks an update as needing work.
- Run one focused research model with a strict report schema, pinned prompt and
  model versions, and `store=false`.
- Expose three narrow read-only tools from two evidence providers: hosted web
  search, SEC EDGAR recent filings, and a bounded filing excerpt.
- Validate, persist, and cite the report before the existing notify step.
- Keep the offline/fixture researcher as an explicit fake. Live mode never
  writes a fake report.
- Keep clocks, providers, tools, and the model injectable. Pytest makes no
  network calls.

### Out of scope

- Changes to market detectors, news filters/classifier, event-manager
  grouping, escalation, cooldowns, or episode rules.
- Discord, delivery cooldown, claim/recovery, or user-configurable rate/cost
  limits. Milestone 7 owns those.
- A worker, queue, second process, scheduler, or multi-agent framework.
- Company Facts / XBRL, valuation, portfolio or tax advice, brokerage, or
  autonomous action.
- New live market or news retrieval from the research path. Research reads
  local history and the three tools above.
- Prompt editing, per-symbol research policy, or treating one investor style
  as the product.

## Behavior

### When research runs

The existing event manager remains the only eligibility owner. This slice
replaces the researcher it already calls:

- A new event or material update produces at most one saved report for the
  current update. Failed or interrupted attempts may repeat model calls.
- An exact repeat or routine same-severity update stays quiet; significant new
  news and newly crossed market windows retain their existing materiality rules.
- Interrupted research retries; a saved report goes to notify without
  researching again.
- A later material update researches the latest complete view.

Live mode requires `INVESTMENT_ASSISTANT_OPENAI_API_KEY`. Without it, queued
research stays retryable and no report is written. Offline fixtures keep the
marked fake researcher.

The live loop processes at most one research run per pass, including startup
as a separate pass. Process saved reports awaiting notification independently
of this limit and before starting research. Research may run while the regular
session is closed. Keep event promotion and material-update rules unchanged.

Research runs synchronously in the existing process. Market ingest, news
polling, and heartbeat may therefore pause for up to the research deadline;
this slice does not promise uninterrupted monitoring during research. After a
run, check the provider session and recover the regular-minute gap through the
existing ingest boundary before starting another run. Do not reconnect a healthy
socket merely because research occupied the loop. Normal news polling resumes
on the next pass.

Persist a research attempt before its first model call. A started attempt,
successful or failed, consumes one of 20 starts per UTC day; a restart cannot
reset the counter. No-key and exhausted-budget checks make zero provider calls
and consume no start. Record their deferred reason without adding the same
failure on every loop pass. Budget exhaustion defers until the next UTC day;
a missing key defers until configuration is available on a later start.

Retry a failed current update no sooner than five minutes after its attempt
finishes. An interrupted attempt remains counted and may retry after restart
once five minutes have elapsed since its recorded start. A materially newer
update is immediately eligible, subject to the daily budget. Prefer updates
with no prior attempt, then the least recently attempted eligible update;
break ties by event creation time and event ID. One failing event must not
consume every pass while other events wait. These are research-attempt bounds,
not notification cooldowns or changes to signal promotion.

### Evidence packet

The application builds the packet from SQLite before the model runs. The model
does not receive credentials, a database handle, filesystem or shell access,
or unrestricted network.

The packet contains:

- Event identity, ticker, optional company name, direction/category,
  importance, market windows, and update number.
- Triggering and related saved signals for that event.
- Bounded local market context: recent completed daily bars, regular-session
  minutes around the trigger, and stored `SPY` comparison when present.
- Related saved articles and classifications.
- The previous report summary for this event, if any.
- The fixed research questions below.

Use a captured `as_of` time for the packet. Include only already available
records and completed bars ending at or before that time. Research may include
evidence learned after the trigger, but must distinguish its publication and
retrieval times from the event time. Do not present later evidence as known at
the trigger. A prior report is historical interpretation, not independent proof.

Fixed packet limits:

- 50 most recent event signals, ordered by occurrence time and ID, with total
  and omitted counts. Preserve the signals establishing the current severity
  and each current market window when selecting this bounded set.
- 25 completed daily bars per symbol for the event ticker and stored `SPY`.
- 60 most recent regular-session minutes per symbol ending no later than the
  latest selected signal time, for the same two symbols.
- 5 linked articles and their ticker-specific classifications, newest first;
  4_000 characters of article text each. Link using persisted signal provenance,
  not headline guessing. Missing old article records remain explicit gaps.
- One previous report summary, at most 2_000 characters, marked if fake.
- 40_000 characters for the serialized packet. Drop oldest optional article
  text and bar rows first; retain event identity, selected signal measurements,
  source references, and omission counts. If required content alone exceeds
  the limit, fail safely before calling the model rather than cut invalid JSON.

Assign stable source references to included records and retain their provenance.
Missing company, sector, comparison data, or history is reported as unavailable;
it does not cause a new Alpaca fetch. Watchlist notes and cost basis are omitted
until those settings exist. Never send configuration objects to the model.

### Tools

Tools are optional follow-up, not a requirement for a valid report. The
application executes EDGAR functions; OpenAI executes its hosted search under
request limits set by the application. The model never sees secrets or raw
EDGAR provider objects.

| Tool | Source | Limit per run |
| --- | --- | --- |
| `web_search` | OpenAI hosted web search | 3 built-in tool calls |
| `get_recent_filings` | SEC submissions for the ticker's CIK | 2 list calls |
| `get_filing_excerpt` | One SEC archive document | 1 excerpt, 8_000 characters |

`get_recent_filings` returns at most 10 records from the last 90 calendar days
relative to research start, newest first, for forms 8-K, 10-Q, and 10-K only.
Use the event ticker; the model cannot research an arbitrary company. Resolve
its CIK through SEC ticker metadata and reuse it within the process. An unknown
CIK returns unavailable. Do not traverse older submissions files or Company
Facts. Each list call uses at most one metadata lookup when uncached and one
submissions request; the excerpt uses one archive request.

`get_filing_excerpt` accepts a filing identifier returned in this run, not an
arbitrary URL. The application builds and validates the SEC archive URL, rejects
off-host redirects, strips markup/scripts, and returns at most 8_000 characters
with a truncation flag. Read no more than 2 MiB per SEC response; oversized
metadata is unavailable and an oversized filing may return a labeled partial
excerpt. All text is untrusted evidence, never instructions.

SEC requests identify the application using the configured user-agent, are
serialized at no more than two requests per second, and use at most 10 seconds
per request within the remaining run deadline. Do not retry HTTP calls inside
one run. A rate limit, including `Retry-After`, disables further SEC requests
until that delay has elapsed (at least 60 seconds if absent); continue with
other available evidence.

If `INVESTMENT_ASSISTANT_SEC_USER_AGENT` is blank or EDGAR fails, the tool
returns a short safe "unavailable" result. Research continues from the packet
and any successful tools. A single tool miss does not fail the run.

Instruct the model to use the packet first and request only missing evidence.
Validate function names and arguments before executing them.

### Loop control

The application owns the tool loop. Ordinary code starts it, counts it, and
stops it. The model may request tools; it does not decide how long research
runs.

Each run is a bounded sequence, not `while the model wants tools`:

1. Send the packet. Tools are available.
2. If the model returns a valid report with no outstanding function requests,
   validate and stop, even if the response also exhausted a tool allowance.
3. If it requests tools, execute only what remains under the caps, append
   bounded results, and continue.
4. When any cap below is hit, strip tools and allow **one** final
   structured-output turn from the packet plus already gathered results.
5. After that final turn, stop. No extra round.

Hard stops, first limit hit wins:

- 4 model turns with tools available
- 6 tool-budget slots, including hosted search and EDGAR requests
- Per-tool caps in the table above
- 90-second elapsed-time deadline, including packet assembly, model calls,
  tools, pacing waits, and finalization

For application-executed EDGAR calls, the same name plus normalized arguments
in one run is not executed again. Return the prior bounded result, or the prior
unavailable result. Duplicate and invalid requests consume tool-budget slots
so they cannot prolong a run. They do not count as extra executed HTTP calls.
Hosted search executes inside the provider response: do not claim to intercept
or deduplicate its individual searches before execution.

Before each model request with search enabled, set `max_tool_calls` to the
smaller remaining search and total allowance. Count returned `web_search_call`
items, including search/open/find actions, against both limits before executing
any returned EDGAR requests. The API cap covers built-in tools only; the
application separately limits EDGAR functions. If a provider response exceeds
the requested cap, fail safely and make no further calls.

A model turn that requests more calls than remain executes only the remaining
budget, in request order. Return short limit results for unexecuted function
requests so continuation has no unanswered call IDs. Bound each application
tool result to 8_000 characters. This cap does not describe OpenAI's internal
search context. Normalize returned web source metadata for local persistence.

Use an injected monotonic timer for elapsed time and the UTC clock for stored
timestamps. Check the deadline before and after each blocking operation. Pass
the remaining time to transports and enforce it across connection and body
reads; checking only between calls is insufficient. Reject late results. The
final no-tool request must fit inside the same deadline, not receive another
90 seconds. No in-run HTTP or model retries.

Hitting a tool or round cap without an already valid report forces one final
no-tool report. A timeout, refusal, transport failure, or missing/invalid final
report is a retryable research failure: no report, no notify. Daily-budget
exhaustion defers a run before it starts.

No nested agents, no extra research process, and no further model calls after
the finalization turn.

### Research job

The model answers only:

1. What happened?
2. What evidence most likely explains it?
3. Is it company-specific, sector, or broad market?
4. Could it be fundamental, or is that unknown?
5. What competing explanations are credible?
6. What is still missing?
7. Which permitted posture fits the evidence?

It must not invent sources, prices, or filings. If the packet and tools do not
explain a market move, `cause_unknown` is true and the likely explanation says
so. Significant good news and bad news are both in scope; direction does not
dictate posture. The output is not a trade instruction.

### Report contract

Final model output is strict Structured Outputs, then validated again into the
internal report. The application supplies IDs, timestamps, triggering signal
IDs, source metadata, and execution provenance; the model supplies analysis
and evidence references. Required fields in the persisted report:

- Stable `report_id` of `report:{event_id}:{update}`
- `event_id`, `event_update`, `ticker`, optional `company`
- `event_occurred_at`, `created_at`
- `triggering_signal_ids`
- `summary`, `likely_explanation`, `competing_explanations`
- `market_context`, `bullish_considerations`, `bearish_considerations`
- `missing_information`, `uncertainty`
- `scope`: `COMPANY`, `SECTOR`, `BROAD_MARKET`, `MIXED`, or `UNKNOWN`
- `cause_unknown`
- `evidence_character`: `UNKNOWN`, `POSSIBLY_TRANSIENT`, or
  `POSSIBLY_FUNDAMENTAL`
- `sources`: source reference, title, URL/accession or local record identity,
  kind (`packet`, `news`, `filing`, `web`), publication time when known, and
  retrieval time
- `confidence` from 0 through 1
- `posture`: `MONITOR`, `INVESTIGATE_FURTHER`,
  `POTENTIAL_OPPORTUNITY_TO_REVIEW`, or `WAIT_FOR_CLARITY`
- `is_fake=false` on the live path
- Model name, prompt version, schema version, attempt ID, packet `as_of`,
  tool-budget usage, executed-call counts, and returned token usage when present

Use source-referenced text items for explanations, market context, and bullish
and bearish considerations: each item contains text and evidence references.
Every reference must resolve to the packet or a successful tool result in this
run. For hosted search, validate against returned URL citations and source
metadata, never a URL invented in report text. Packet-only reports cite local
signal/bar identities; do not require a fictitious public URL.

Require at least one source for the observed trigger. A claimed explanation
must cite corroborating evidence beyond the price movement itself, or be
explicitly labeled a hypothesis with uncertainty. Source identity validation
does not prove factual correctness; representative report review remains part
of validation. Reject unknown references, mismatched event identities, invalid
enums/confidence, or extra schema fields. Missing evidence must not become
invented facts or numeric context.

Limit summary to 1_000 characters, each other analysis text item to 2_000,
each list to 10 items, and the report to 20 sources. Preserve decimal price
values and timestamps from the packet. Do not let the model replace them.

A market-only packet with no corroborating news, filing, or web evidence must
be allowed to set `cause_unknown=true` and `WAIT_FOR_CLARITY` or `MONITOR`.
Refused, malformed, incomplete, or out-of-range output is a retryable research
failure: no report, no notify.

Keep the console notifier. It prints the real summary, not the fake prefix.
Include posture, uncertainty, and a compact source list in the console output
so this milestone's result is reviewable before Discord exists.

### Persistence and compatibility

Migrate existing SQLite databases without dropping signals, events, old reports,
notification attempts, or failures. Keep the unique `(event_id, event_update)`
report constraint and current-update check before save and delivery. Persist
the report before notifying; a delivery retry reads the saved report unchanged.
Discard a stale result if the event has moved to a newer material update.

Persist bounded packet snapshots, normalized evidence actually supplied to the
model, attempt status/timing, usage, and safe failures. This enables replay with
fake responses without re-fetching live sources. Do not store raw HTTP/model
dumps or private reasoning. A crash after a model call but before report save
can repeat a billed call; this slice guarantees one saved report per update,
not exactly one external request.

Legacy fake reports remain readable and visibly fake. Do not automatically
re-research or re-notify already completed updates during migration. A pending
legacy report can resume its existing console delivery with its fake label.
New production research must never fall back to the fake on missing keys or
provider failure. Offline runs and explicitly injected test doubles remain
available.

### Bounds and provenance

Fixed first-slice limits:

- Wall-clock timeout: 90 seconds per run
- Model turns with tools: 4, then one forced no-tool finalization
- Tool-budget slots: 6, including web search and duplicate/invalid requests
- Research starts (success or fail): 20 per UTC day
- One live research run at a time; one per live-loop pass
- Maximum output tokens per model request: 4_000, including reasoning
- Model requests per run: at most 5, including finalization
- Application-managed serialized model input per request: 100_000 characters;
  fail safely before sending if it cannot fit after optional evidence trimming
- OpenAI response body: at most 2 MiB; reject larger responses before parsing
- Safe error text: at most 300 characters from application-owned descriptions

Persist report fields, citations, prompt/model versions, tool counts, and safe
failure text. Credentials, raw HTML, and provider/model dumps stay out of
logs, prompts, fixtures, and exceptions.

Use OpenAI Responses API with pinned `gpt-5.4-mini-2026-03-17`, hosted web
search, application-executed EDGAR tools, strict JSON Schema Structured
Outputs, and `store=false`. This is not the news classifier and does not reuse
its prompt. Keep the bounded conversation in the application; do not depend on
server-stored response chains. Fixed call/token bounds constrain usage; a
configurable dollar budget remains Milestone 7.

## Acceptance criteria

- [x] AC-01: For a queued event, the application assembles a bounded local
  evidence packet from saved event, signal, market, news, and prior-report
  state. The model does not receive secrets or unrestricted system access.
- [x] AC-02: A valid live report matches the schema, cites sources, states
  uncertainty, and uses only a permitted posture. It is saved before notify
  and `is_fake` is false.
- [x] AC-03: A market-only event with no corroborating news or filing may
  persist `cause_unknown=true` and must not invent a cause or source.
- [x] AC-04: Significant news-only and market-plus-news events can each
  produce one report for the current update without changing event-manager
  eligibility rules.
- [x] AC-05: Web search and EDGAR tools are optional, bounded, read-only, and
  application-controlled. Tool failure or a missing SEC user-agent degrades
  safely and does not by itself skip a packet-based report.
- [x] AC-06: The application stops the tool loop at the first cap (turns,
  calls, per-tool limit, or duplicate-call exhaustion). Duplicate EDGAR
  requests are not re-executed; hosted search uses the API cap and returned
  counts. A cap without a valid report forces one no-tool final report.
- [x] AC-07: Wall-clock timeout, refusal, schema mismatch, missing final
  report are retryable failures with no report and no notification. Missing
  keys and daily-budget exhaustion defer without a provider call or fake report.
- [x] AC-08: Restart of interrupted research retries the current update; a
  saved report resumes at notify without a second model call. One update
  still yields at most one report.
- [x] AC-09: Offline fixtures and pytest keep the fake researcher. Live mode
  without an OpenAI key writes no fake report. Pytest uses fakes for the
  model, tools, and clock.
- [x] AC-10: No Discord adapter, worker, detector change, classifier change,
  or event-policy change is added. Ruff, mypy, and pytest pass.
- [x] AC-11: One research attempt per live pass, notification-only recovery,
  fair selection, five-minute retry spacing, and post-research minute recovery
  work with fake clocks/providers in open and closed sessions.
- [x] AC-12: Attempt reservations, daily budgets, packet/evidence snapshots,
  and retry times survive restarts. Migration preserves existing fake reports
  and completed updates without reprocessing them.
- [x] AC-13: Unknown citations, arbitrary filing URLs, prompt-injection text,
  oversized input/output, and late responses cannot bypass validation, tool
  limits, or the total deadline. Source metadata comes from observed evidence.

## Constraints

- One local Python process and built-in `sqlite3` only.
- Use Python `>=3.14,<3.15` and `uv`.
- Convert OpenAI and EDGAR values into internal models at their adapters.
- Keep eligibility, deduplication, and cooldowns outside the research model.
- Keep time and external I/O controllable.
- Preserve source, retrieval time, model, and prompt version where they affect
  replay.
- Research tools stay narrow, read-only, bounded, and application-controlled.

## Resolved decisions

- Keep event-manager eligibility unchanged; this slice replaces the fake
  researcher only.
- Put local market, news, and prior-report context in the packet. Do not add
  research tools that re-fetch Alpaca or SQLite.
- First tools are hosted web search and EDGAR filings/excerpt. Company Facts
  stay out.
- Pin `gpt-5.4-mini-2026-03-17` with Responses API tools and strict Structured
  Outputs, `store=false`.
- Use the posture list and `cause_unknown` flag above. No buy/sell field.
- Process at most one research run per live-loop pass with the bounds above.
- The application owns a hard-capped tool loop. Tool-budget exhaustion
  finalizes once unless a valid report is already available; elapsed-time
  timeout fails the run. Duplicate EDGAR requests are not re-executed.
- Leave Discord and configurable cost limits to Milestone 7.

## Implementation references

- [D-007 bounded research](../../docs/DECISIONS.md)
- [OpenAI Responses API and built-in tool limits](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)
- [GPT-5.4 mini snapshot and supported features](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
- [OpenAI web search](https://developers.openai.com/api/docs/guides/tools-web-search)
- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [OpenAI stateless reasoning continuation](https://developers.openai.com/api/docs/guides/reasoning)
- [SEC developer resources and fair access](https://www.sec.gov/about/developer-resources)

Provider references checked on 18 September 2026. They establish API behavior,
not a live account-access or report-quality test. Fixed limits above are this
feature's application policy.
