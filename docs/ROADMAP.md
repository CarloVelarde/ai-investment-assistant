# AI Investment Assistant — Roadmap

**Status:** Active

## Current focus

Milestones 0–7 are implemented: the local app monitors markets and news, retains
event history, researches selected updates, and delivers saved reports through
durable Discord or console paths. **Milestone 8 — Full-loop hardening** is next;
it verifies the complete local MVP with replay and bounded live checks.

Completed milestones record implementation and acceptance checks. Live verification
is noted separately; the full loop still needs Milestone 8's validation. Each linked
spec owns detailed behavior, with its plan and tasks tracking implementation.

## Milestones

### Milestone 0 — Repository foundation

**Status:** Complete

Provide the runnable Python package, `uv` dependency management, configuration and logging foundations, documentation templates, and local and CI checks.

**Completed:** A fresh checkout can be set up, run, and validated from the README without secrets or external services.

### Milestone 1 — Offline walking skeleton

**Status:** Complete

**Spec:** [`specs/001-offline-walking-skeleton/`](../specs/001-offline-walking-skeleton/SPEC.md)

Prove fixtures → normalization → deterministic detection → correlation → event → fake research → console notification without live services or persistence.

**Completed:** The deterministic paired-signal pipeline, packaged fixtures, end-to-end scenarios, and repository checks pass. Pairing was a demonstration, not a product gating rule.

### Milestone 2 — Durable event foundation

**Status:** Complete

**Spec:** [`specs/002-durable-event-foundation/`](../specs/002-durable-event-foundation/SPEC.md)

Add SQLite-backed signal, evolving-event, research, notification, and failure state. One event manager accepts independent offline market and news signals and owns promotion, exact deduplication, same-severity suppression, material escalation, delivery-state idempotency, and restart-safe stage recovery. Use persisted lifecycle state as the local research queue; add no worker service or live integration.

**Completed:** SQLite-backed lifecycle state now routes independent market and news signals through one event manager. Replay, escalation, notification, failure, and restart tests prove durable idempotent processing without a worker or live service.

### Milestone 3 — Market history and offline detection

**Status:** Complete

**Spec:** [`specs/003-market-history-and-offline-detection/`](../specs/003-market-history-and-offline-detection/SPEC.md)

Persist normalized replay bars and implement two deterministic evaluation modes over the same history and signal contract:

- A fast detector for abrupt movement on completed bars.
- One fixed after-close daily scan for five- and twenty-trading-day movement, recent-high drawdown, and performance relative to `SPY`.

Explicit thresholds, severity levels, crossing, rearm, and episode-closing rules live in the feature spec. Add no separate weekly process or configurable cadence initially.

**Completed:** Offline abrupt-drop, gradual-decline, continuation, escalation, recovery, and broad-market scenarios produce understandable signals and one correctly updated event episode without duplicate research. Recovery closes the episode; a later drop starts a new event. Completed-bar transitions commit atomically, use bounded as-of history, retain their crossing measurements, and tolerate delayed same-session `SPY` context.

### Milestone 4 — Live market data

**Status:** Complete

**Spec:** [`specs/004-live-market-data/`](../specs/004-live-market-data/SPEC.md)

Add Alpaca market history and streaming behind the existing input boundary. Add stream health, reconnection, stale-data detection, missing-bar backfill, and invocation of the fast and daily detectors without changing their core rules.

**Completed:** Settings, normalization, the market-data port, REST backfill, quiet replay, stream ingest, after-close daily, reconnect/gap fill, and stale-stream rules. `main` uses the live path when keys exist. Production REST and the trading clock use stdlib HTTP. Live mode opens **one** stock websocket (`wss://stream.data.alpaca.markets/v2/{feed}`), authenticates, subscribes the watchlist plus `SPY` to `bars` and `updatedBars`, and feeds completed minutes through the existing ingest path. The news socket is not opened.

**Live trial (19 Aug 2026):** the socket, minute ingest, fast detector, heartbeat, and clean Ctrl+C behaved as specified. The unfinished-daily and session-gap follow-up was completed in [spec 006](../specs/006-live-session-hardening/SPEC.md), not by reopening socket tasks here.

**Complete when:** a small watchlist reliably feeds normalized live and recovered bars through both market evaluation modes during regular market operation.

### Spec 006 — Live session hardening

**Status:** Complete

**Spec:** [`specs/006-live-session-hardening/`](../specs/006-live-session-hardening/SPEC.md)

Stop after-close daily rules from running on today’s still-moving price. Add a session-open gap rule that compares the last regular close to today’s regular open. Keep the existing fast and daily thresholds. Add no news client.

**Completed:**

- `multi_day_move`, `drawdown_from_high`, and `relative_to_spy` run only after the regular session has closed, on finished daily bars. They do not run on today’s still-moving price, and a restart during the session does not change that story.
- A move of 3% or more from the last regular close to today’s regular open emits `session_gap` once that session. A same-session restart does not research the same gap again.

**Live verification (19 Aug 2026):** three starts against one SQLite database used real IEX history and stream minutes. Today’s moving daily rows stayed incomplete, `session_gap` evaluated once at the regular open, REST backfill filled a minute missed while stopped, later socket minutes continued in order, and restarts created no duplicate signals, research, or notifications.

### Spec 007 — Regular session lifecycle correction

**Status:** Complete

**Spec:** [`specs/007-regular-session-lifecycle/`](../specs/007-regular-session-lifecycle/SPEC.md)

Finish the existing live market lifecycle before adding news:

- Retain and evaluate regular-session `1Min` bars only; extended-hours data must
  not change the fast detector.
- Use the stock socket only while the provider reports the regular session open.
  REST recovery and pending event work remain available while closed.
- Recover the most recent unprocessed completed daily scan after a next-day or
  weekend restart, without late-firing old fast or session-gap signals.
- Fill the small startup handoff between the REST snapshot and socket subscription.

This correction keeps one process and the existing detector/event rules. It adds
no news client, exchange calendar, worker, second socket, or extended-hours rule.

**Completed:** Regular-only minute ingest and bounded detector history, a
provider-session-aware socket lifecycle, socket-independent recovery and pending
work, one post-subscription REST handoff fill, and latest-completed-daily catch-up
are covered by deterministic restart and transition tests.

**Live verification (20 Aug 2026):** An isolated after-close start against real
IEX data recovered 1,215 bars for TSLA, AMD, and `SPY`, retained 1,122 regular
minutes through 15:59 ET with no extended-hours rows, ran the completed daily
scan for all three symbols, and stayed alive with `session_open=false` and
`waiting_on_socket=false`. No socket or reconnect was attempted, no signal
qualified, and Ctrl+C stopped the process cleanly.

### Milestone 5 — Live news and classification

**Status:** Complete

**Spec:** [`specs/008-live-news-classification/`](../specs/008-live-news-classification/SPEC.md)

Add bounded Alpaca REST news polling, deterministic relevance and duplicate filtering, classifier-call limits, and a small inexpensive structured AI classifier on the news path only. The classifier may mark an article significant whether the story is positive, negative, or unclear. Significant news may create an event alone or enrich and requeue an existing market episode; rejected news creates no event or cooldown. The classifier does not decide research or notification; the existing event manager still does. A news websocket is deferred as a possible post-MVP optimization if observed polling timeliness is inadequate.

**Complete when:** significant news of either direction is processed once through the shared event manager without requiring a market trigger or researching every article.

**Completed:** Bounded Alpaca REST news polling, deterministic watchlist/recency/source/identity/budget filters, and a small structured classifier on the news path only. Significant good, bad, or unclear news can create or enrich an event through the existing event manager. Rejected news creates no event or cooldown. The news websocket and Discord stayed later work. Research followed in Milestone 6.

### Milestone 6 — Research and reporting

**Status:** Complete

**Spec:** [`specs/009-research-and-reporting/`](../specs/009-research-and-reporting/SPEC.md)

The [plan](../specs/009-research-and-reporting/PLAN.md) and
[tasks](../specs/009-research-and-reporting/TASKS.md) cover bounded local evidence,
focused research with hosted search and SEC filings, validated cited reports,
durable attempts, and live-loop integration. Console delivery remains in this
slice; Discord stays in Milestone 7.

**Completed:** Each eligible new or materially updated event can produce one
bounded report with citations, uncertainty, and a permitted posture. Market-only
and broad-market cases may say the cause is unknown. Significant positive,
negative, and unclear news can each produce a report without a market signal,
and market-plus-news stays one event. The event manager still decides eligibility.
Live mode runs at most one research attempt per pass and prints the saved report
on the console. Missing keys and provider failures do not write a fake report.
No Discord adapter, worker, detector change, classifier change, or event-policy
change was added. Automated checks use fakes only; no live smoke was run.

### Milestone 7 — Discord and operations

**Status:** Complete; deterministic acceptance checks passed

**Spec:** [`specs/010-discord-and-operations/`](../specs/010-discord-and-operations/SPEC.md)

Deliver saved reports to Discord with controlled retries and recoverable failures.
Make operational health visible and model use subject to configurable call and
estimated-cost limits. Preserve quiet routine repeats and timely material updates.

**Complete when:** saved reports produce the intended alerts, delivery failures
remain visible and recoverable, and usage controls hold across restarts. Acceptance
checks pass; integrated live verification follows in Milestone 8.

**Completed:** SQLite v7 records delivery claims, submissions, receipts, retry
state, and shared estimated model-cost reservations. One webhook adapter sends saved
reports in live mode; local commands list, retry, and confirm held work. Console
completion is durable before its output. Migration retains prior history and holds
model calls on a legacy-spend migration day. One due external send runs before
one research attempt per live pass, with post-work minute recovery. Heartbeat
reports current delivery backlog and separate model admission reasons. Deterministic
  tests cover the acceptance criteria using fakes. A separately scoped live
  Discord smoke test sent one labeled fake report, verified its receipt by
  lookup, and confirmed a restart did not resubmit it (25 September 2026).
  Full-loop live verification remains Milestone 8 work.

### Milestone 8 — Full-loop hardening

**Status:** Not started; spec drafted

**Spec:** [`specs/011-full-loop-hardening/`](../specs/011-full-loop-hardening/SPEC.md)

After Milestone 7, verify the complete monitoring → event → research → notification
loop through representative replay and bounded live checks. Fix observed reliability
gaps and assess report usefulness. Tune rules only from observed behavior.

**Complete when:** replay and live evidence demonstrate the
[product success criteria](PRODUCT.md#mvp-success-criteria), with usable operating
and recovery guidance and documented limitations. This completes the local MVP.

## After the MVP

A news websocket, configurable rules or scan cadences, additional horizons,
portfolio context, deployment, interfaces, valuation tools, and provider failover
require demonstrated need and a new roadmap decision. Deferred scope and current limitations live in
[Product](PRODUCT.md#constraints-and-limitations) and [Decisions](DECISIONS.md#deferred).
