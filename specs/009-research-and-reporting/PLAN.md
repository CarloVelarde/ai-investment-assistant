# Implementation Plan: Research and Reporting

**Document status:** Proposed

**Implementation status:** Not started

Behavior and fixed limits belong to [SPEC.md](SPEC.md). Track implementation
and validation in [TASKS.md](TASKS.md).

## Approach

Replace the existing live researcher through one complete vertical slice:

1. Extend the internal report contract and migrate SQLite without losing old
   reports or lifecycle state.
2. Build a bounded evidence packet from existing local records.
3. Add the research model and EDGAR boundaries with injected fakes.
4. Implement the bounded research loop, citation validation, and durable attempts.
5. Integrate one research attempt per live pass and console report delivery.
6. Prove recovery, limits, and compatibility through deterministic tests.

Keep the current single process, event manager, and console notifier. This
milestone implements D-005, D-007, D-008, and D-009; it does not change stable
product or architecture decisions. Exact report fields and execution limits
remain feature-level choices in the spec.

## Existing code and required changes

| Existing boundary | Required change |
| --- | --- |
| `models.ResearchReport` | Add validated analysis, evidence references, source metadata, and execution provenance while retaining explicit fake reports. |
| `reporting.Researcher` | Keep the event-and-signals callable boundary; compose a live researcher with storage, model, tools, and clocks. |
| `SQLiteStorage` | Add bounded evidence reads, report details, attempt reservations, snapshots, and retry state. |
| `EventManager.process_pending` | Add bounded live scheduling and notification-only recovery without changing signal grouping or promotion. |
| `EventManager._research` | Preserve the transition to researching, current-update validation, atomic report save, and failure recovery. |
| `main.run_live_session` | Construct the real researcher while storage is open; pass the live processing limit and recover minute gaps after research. |
| `reporting.emit_console_notification` | Render the validated summary, posture, uncertainty, and sources; retain fake labels. |
| `config.Settings` | Add the optional SEC user-agent; reuse the existing OpenAI key. |
| `clock.Clock` | Retain UTC timestamps and inject an elapsed-time function separately for deadlines. |

`process_pending` currently drains all pending events, and `main` defaults to
the fake researcher even in live mode. Both need explicit live wiring. Preserve
offline drain behavior and explicit researcher injection in tests. A missing
production OpenAI key must select a deferred state, never an implicit fake.

## Internal structure

Keep code under `src/investment_assistant/`. Introduce modules only when used:

- `research.py`: packet assembly, evidence registry, fixed bounds, research
  orchestration, and model boundary.
- `research_model.py`: OpenAI Responses transport, request schema, and conversion
  from provider responses to internal report drafts and tool requests.
- `sec.py`: ticker-to-CIK lookup, submissions metadata, and safe filing excerpts.
- Extend `models.py`, `storage.py`, `event_manager.py`, `reporting.py`, and
  `main.py` for the contracts and integration described above.

Use interfaces at model, HTTP, and elapsed-time boundaries only. Packet assembly,
budget decisions, tool routing, source validation, and retry selection remain
ordinary typed functions. Use standard-library HTTP where it can enforce the
required deadline; do not introduce an agent framework or background worker.

## Data and migration

The existing database version is 4. Add one atomic forward migration:

- Keep report identity, summary, timestamps, `is_fake`, and the unique event/update
  constraint. Add a schema-versioned JSON details column for the validated live
  report, read back into internal types. Older fake rows may have null details;
  new live rows may not.
- Add research attempts keyed by attempt ID, with event/update, UTC start and
  finish times, status, retry-not-before, model/prompt versions, bounded packet
  and evidence snapshots, usage counters, and safe failure details.
- Reserve a start transactionally after checking the UTC-day count and current
  update. Count reserved attempts even if the process stops before receiving a
  response. Use the attempt rows as the budget ledger; no separate queue service.
- Persist no-key/budget deferral state separately from started attempts, using
  one current record per event/update so repeated passes do not grow the log.
- Finalize successful attempt state with the report save. Reconcile unfinished
  attempt bookkeeping against saved reports on restart; never redo research
  when a report already exists.

Keep database transactions short and release them before network calls. Maintain
the existing current-update checks on failure and report saves. A stale attempt
can retain its audit record but cannot overwrite a newer event update.

Do not fabricate citations or historical evidence for old reports. Migration
does not queue previously notified fake reports for live research. Test fresh
databases and every previously supported migration path through version 4.

## Evidence assembly and source validation

Capture the event/update and packet `as_of`, then use bounded SQL queries for
signals, completed bars, linked news/classifications, and the previous report.
Avoid loading entire market or article tables and truncating only afterward.
Deduplicate the ticker and `SPY` when the event ticker is itself `SPY`.

Use existing news signal provenance to resolve articles and classification
versions; inspect stored records rather than infer identity from headline text.
If a backward-compatible reference field is needed, add only that linkage and
keep detector/classifier policy unchanged. Old missing links become explicit
missing information. Retain the trigger's original measurement even when a
later stored bar or article has been revised; label revision/retrieval times.

Apply the spec's count and serialized-size limits before model invocation.
Persist the normalized packet actually sent, including truncation and missing
data indicators. Build a source registry from that packet and successful tool
results. Local records use stable record references; web sources use normalized
URLs from returned search metadata. Resolve report references against this
registry and populate source details from it, not from model assertions.

Require strict live report fields and list/text bounds. Keep optional legacy
details confined to fake-report compatibility so they cannot weaken production
validation. Use malformed, unrelated, and fabricated citation fixtures to prove
rejection. Test unknown-cause reports with only local trigger evidence.

## Provider adapters and bounded execution

Pin the model and prompt version specified in the spec. The OpenAI adapter uses
`store=false`, strict structured report output, bounded output tokens, and only
the three permitted tools. Parse all response items; do not assume the first
output item is report text. Handle refusal, incomplete output, function requests,
hosted search metadata, and transport errors explicitly.

Hosted search and EDGAR have different execution owners. Set `max_tool_calls`
on each Responses request to the remaining built-in allowance and decrement
the run budget from returned search items before executing local functions.
Deduplicate only the EDGAR calls the application can intercept. Return a result
for each requested function call ID, including calls denied by a limit. Once a
cap is hit without a valid report, permit exactly one no-tool finalization.

With `store=false`, retain only the bounded continuation items required by the
Responses protocol in memory, including opaque reasoning items if required;
do not log or persist them as evidence. Persist normalized evidence and usage,
not private reasoning or complete provider payloads. Include search source
metadata and normalize its references before validating any returned report.

The SEC adapter owns fixed HTTPS endpoints, user-agent headers, CIK lookup,
archive-path construction, response-size limits, pacing, and deadline handling.
Only a filing identity returned by this run can select an excerpt. Model text
cannot override the company, host, headers, path policy, or timeouts. Missing
credentials, unsupported content, malformed metadata, and provider failures
produce safe unavailable results as specified.

Enforce the shared deadline through transport connection/read operations and
check after every response. Reject results arriving after the deadline. Use
fakes for monotonic time, UTC time, waiting, HTTP, and model responses; do not
use real sleeps in tests. Failed tool retrieval is optional evidence loss;
expired run time or an invalid final report fails research.

## Live lifecycle and recovery

1. Complete the pass's market/news input work through existing boundaries.
2. Deliver saved reports ready for notification without using research budget.
3. Select at most one eligible research update, respecting retry times and
   the spec's fair ordering. Reserve its attempt before the first model call.
4. Assemble evidence, run research, validate, and atomically save the report
   before invoking the console notifier. Failure leaves no partial report.
5. Recheck session state and recover regular-minute gaps before another research
   attempt. Continue due news polling and heartbeat on later passes.

Keep interrupted research retryable and preserve successful delivery state.
Budget/key deferrals do not suppress new signals, close episodes, or start a
notification cooldown. A saved report awaiting delivery must progress even
when research budget is exhausted or no OpenAI key is available.

Test startup separately: it currently processes pending work before opening
the socket. Prove the existing post-subscription handoff and the new
post-research recovery compose without duplicate signals or reports. Recovery
uses a fresh session check if research crosses the regular close.

## Validation

Add public-behavior tests alongside each implementation task:

- Report contracts, source references, migration, and legacy fake compatibility.
- Packet size/order, missing data, corrected records, completed bars, and `as_of`.
- SEC normalization, ticker binding, approved filing identity, redirects,
  oversized responses, rate limits, missing user-agent, and secret-safe errors.
- OpenAI request shape and typed parsing using fake HTTP, including search
  count enforcement and malformed/refused/incomplete structured output.
- Successful packet-only, news-only, combined, broad-market, and unknown-cause
  research; significant positive, negative, and unclear news remain reviewable.
- Duplicate functions, excess requests, per-tool caps, finalization, total
  deadline, output bounds, and persistent UTC-day budgets.
- Interrupted attempts, saved-report restart, delivery failure, new material
  update during research, fair scheduling, and minute-gap recovery.

Run all repository checks:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
```

Update milestone scope guards only for newly permitted research behavior;
retain assertions excluding Discord, trading, workers, and news websockets.
Use a separate disposable database for any opt-in live smoke. A live smoke
should verify source usefulness and account/tool access, but is never part of
pytest and must not commit keys, private data, or raw provider payloads.

## Tradeoffs and completion

Synchronous research can delay the loop by the run deadline. Fixed budgets,
one attempt per pass, and existing recovery keep this slice small; they do not
guarantee continuous low-latency ingest. A future concurrency change needs
observed evidence and its own plan.

Citation validation proves that a source was available, not that every inference
is correct. Reports must still state uncertainty, and live report review remains
necessary. At-most-one saved report does not mean exactly-once provider billing
after a crash. Token/call caps are not configurable dollar-budget enforcement.

Finish when all spec acceptance criteria and repository checks pass, owning docs
describe the implemented behavior, and limitations/manual verification are
recorded accurately. Discord remains Milestone 7.
