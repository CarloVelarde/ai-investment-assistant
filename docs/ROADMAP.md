# AI Investment Assistant — Roadmap

**Status:** Active

## Current focus

**Milestone 2 — Durable event foundation** is next. Its feature spec must be approved before implementation; Milestone 1 is complete.

## Milestones

### Milestone 0 — Repository foundation

**Status:** Complete

Provide a runnable Python package, configuration and logging foundations, `uv` dependency management, documentation templates, and local and CI quality checks.

**Completed:** A fresh checkout can be set up, run, and validated from the README without secrets or external services.

### Milestone 1 — Offline walking skeleton

**Status:** Complete

**Spec:** [`specs/001-offline-walking-skeleton/`](../specs/001-offline-walking-skeleton/SPEC.md)

Prove the complete internal flow with local fixtures, deterministic rules, fake research, and console notification. Use one triggering and one non-triggering scenario; add no external service or database.

**Completed:** The deterministic pipeline, triggering and non-triggering end-to-end tests, packaged fixtures, and repository checks pass.

### Milestone 2 — Durable event foundation

**Status:** Not started

Add SQLite persistence, event lifecycle state, deduplication, cooldowns, notification state, and restart-safe processing.

**Complete when:** events and delivery state survive restarts without duplicate work or alerts.

### Milestone 3 — Live market data

**Status:** Not started

Add Alpaca market data, historical baselines, stream health and reconnection, stale-data detection, and missing-bar backfill.

**Complete when:** a small watchlist reliably produces normalized market signals during regular market hours.

### Milestone 4 — Live news and classification

**Status:** Not started

Add Alpaca news, deterministic filtering and deduplication, classifier-call limits, and a small structured AI classifier.

**Complete when:** relevant news can enrich or create events without classifying or researching every article.

### Milestone 5 — Research and reporting

**Status:** Not started

Add evidence packets, bounded read-only tools, focused AI research, source tracking, and validated reports.

**Complete when:** a significant event produces a bounded report with citations, uncertainty, and a permitted research posture.

### Milestone 6 — Discord and operations

**Status:** Not started

Add idempotent Discord delivery, bounded retries, operational status, and API rate and cost enforcement.

**Complete when:** one completed report produces one useful alert and failures remain visible and recoverable.

### Milestone 7 — Full-loop hardening

**Status:** Not started

Replay representative scenarios and failures through live-operation boundaries and tune rules from observed behavior.

**Complete when:** the local MVP meets the [product success criteria](PRODUCT.md#mvp-success-criteria) with documented limitations.

After the MVP, deployment, interfaces, valuation tools, or additional providers require demonstrated need and a new roadmap decision.
