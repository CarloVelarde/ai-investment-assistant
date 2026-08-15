# AI Investment Assistant — Roadmap

**Status:** Active

## Current focus

**Milestone 4 — Live market data** is complete. The next product feature is Milestone 5 (live news and classification). Do not start it until that spec is approved.

A small tooling spec, [`specs/005-ops-visibility/`](../specs/005-ops-visibility/SPEC.md), covers opt-in heartbeat and watch logging. It can be implemented without blocking Milestone 5 and does not change detectors or events.

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

**Complete when:** a small watchlist reliably feeds normalized live and recovered bars through both market evaluation modes during regular market operation.

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
