# AI Investment Assistant — Roadmap

**Status:** Active

## Current focus

**Milestone 2 — Durable event foundation** is in progress under its approved feature spec.

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

**Status:** In progress

**Spec:** [`specs/002-durable-event-foundation/`](../specs/002-durable-event-foundation/SPEC.md)

Add SQLite-backed signal, evolving-event, research, notification, and failure state. One event manager accepts independent offline market and news signals and owns promotion, exact deduplication, same-severity suppression, material escalation, delivery-state idempotency, and restart-safe stage recovery. Use persisted lifecycle state as the local research queue; add no worker service or live integration.

**Complete when:** replay and restart tests prove that a new signal is processed once, an exact duplicate or same-severity repeat stays quiet, a worse severity or significant new news requeues one update, and interrupted research or notification resumes from durable state.

### Milestone 3 — Market history and offline detection

**Status:** Not started

Persist normalized replay bars and implement two deterministic evaluation modes over the same history and signal contract:

- A fast detector for abrupt movement on completed bars.
- One fixed after-close daily scan for five- and twenty-trading-day movement, recent-high drawdown, and performance relative to `SPY`.

Define explicit thresholds, severity levels, crossing, rearm, and episode-closing rules in the feature spec. Add no separate weekly process or configurable cadence initially.

**Complete when:** offline abrupt-drop, gradual-decline, continuation, escalation, recovery, and broad-market scenarios produce understandable signals and one correctly updated event episode without duplicate research.

### Milestone 4 — Live market data

**Status:** Not started

Add Alpaca market history and streaming behind the existing input boundary. Add stream health, reconnection, stale-data detection, missing-bar backfill, and invocation of the fast and daily detectors without changing their core rules.

**Complete when:** a small watchlist reliably feeds normalized live and recovered bars through both market evaluation modes during regular market operation.

### Milestone 5 — Live news and classification

**Status:** Not started

Add Alpaca news, deterministic relevance and duplicate filtering, classifier-call limits, and a small structured AI classifier. Significant news may create an event alone or enrich and requeue an existing market episode; rejected news creates no event or cooldown.

**Complete when:** significant news is processed once through the shared event manager without requiring a market trigger or researching every article.

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
