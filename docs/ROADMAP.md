# AI Investment Assistant — Roadmap

**Status:** Active

## Current focus

**Spec 007 — Regular session lifecycle correction** is approved and not started.
It is the final corrective slice before **Milestone 5 — Live news and
classification**, which remains not started.

The live socket (Milestone 4), opt-in heartbeat / watch log ([spec 005](../specs/005-ops-visibility/SPEC.md)), and live-session hardening ([spec 006](../specs/006-live-session-hardening/SPEC.md)) are in. Spec 006 fixed two problems found by the 19 Aug 2026 live trial:

1. Unfinished same-day prices are no longer treated as the official close, so after-close daily rules wait for a finished session.
2. A dedicated `session_gap` rule now checks the prior completed regular close against the first regular-session minute’s open once per symbol per session.

Details: [`specs/006-live-session-hardening/`](../specs/006-live-session-hardening/SPEC.md).

Follow-up restart analysis found three remaining correctness gaps in the live
market loop: extended-hours minutes can enter regular fast history, the stock
socket opens while the regular session is closed, and a missed daily scan can be
lost when the first restart occurs on a later date. [Spec 007](../specs/007-regular-session-lifecycle/SPEC.md)
addresses those gaps and the small startup REST-to-stream handoff window without
adding a scheduler, calendar service, or extended-hours product.

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

**Status:** Approved; implementation not started

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

### Milestone 5 — Live news and classification

**Status:** Not started

Add Alpaca news, deterministic relevance and duplicate filtering, classifier-call limits, and a small inexpensive structured AI classifier on the news path only. The classifier may mark an article significant whether the story is positive, negative, or unclear. Significant news may create an event alone or enrich and requeue an existing market episode; rejected news creates no event or cooldown. The classifier does not decide research or notification; the existing event manager still does.

**Complete when:** significant news of either direction is processed once through the shared event manager without requiring a market trigger or researching every article.

### Milestone 6 — Research and reporting

**Status:** Not started

Add evidence packets, bounded read-only tools, focused AI research, source tracking, and validated reports. Market-only research must allow an honest “cause unknown” result.

**Complete when:** each eligible new or materially updated event produces one bounded report with citations, uncertainty, and a permitted research posture.

### Milestone 7 — Discord and operations

**Status:** Not started

Add idempotent Discord delivery, bounded retries, operational status, and API rate and cost enforcement. Notification cooldown begins only after successful delivery and does not block material escalation.

**Complete when:** completed reports produce the intended Discord alerts, routine repeats stay quiet, and failures remain visible and recoverable.

### Milestone 8 — Full-loop hardening

**Status:** Not started

Replay representative market, news, restart, provider-failure, research-failure, and delivery-failure scenarios through the same boundaries used by live operation. Tune rules only from observed behavior.

**Complete when:** the local MVP meets the [product success criteria](PRODUCT.md#mvp-success-criteria) with documented limitations.

After the MVP, configurable scan cadences, additional horizons, deployment, interfaces, valuation tools, or provider failover require demonstrated need and a new roadmap decision.
