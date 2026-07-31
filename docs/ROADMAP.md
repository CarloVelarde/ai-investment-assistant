# AI Investment Assistant — Roadmap

**Status:** Active  
**Last reviewed:** July 31, 2026

## Document Purpose

This document defines the order of development from repository setup through the MVP. It tracks milestone outcomes, not low-level tasks or fixed dates.

Product scope belongs in [`PRODUCT.md`](PRODUCT.md). Stable system boundaries belong in [`ARCHITECTURE.md`](ARCHITECTURE.md). Detailed scope, plans, tasks, and acceptance criteria belong under `specs/`.

## Current Focus

**Milestone 0 — Repository foundation** is in progress. Product code begins only after the repository is documented, runnable, and able to pass its local quality checks.

## Milestones

### Milestone 0 — Repository foundation

**Status:** In progress

Create the minimum foundation for consistent solo and agent-assisted development:

- Permanent documentation and feature-spec templates.
- Python package, configuration, logging, and test foundations.
- Reproducible dependency management with `uv`.
- Ruff, mypy, pytest, and matching GitHub Actions checks.
- Clear setup, run, format, lint, type-check, and test commands.

**Complete when:** a fresh checkout can be set up, run, and validated from the README without secrets or external services.

### Milestone 1 — Offline walking skeleton

**Status:** Not started  
**Feature spec:** `specs/001-offline-walking-skeleton/`

Prove the real internal flow using fakes:

> Fixtures → normalization → deterministic detection → signal correlation → event → fake research report → console notification

Use one triggering and one non-triggering scenario. Do not add internet access, API keys, SQLite, real AI calls, Alpaca, SEC data, or Discord.

**Complete when:** the pipeline runs deterministically and its end-to-end tests pass.

### Milestone 2 — Durable event foundation

**Status:** Not started

Add SQLite persistence, event lifecycle state, deduplication, cooldowns, notification state, and restart-safe processing.

**Complete when:** events and delivery state survive restarts without duplicate work or alerts.

### Milestone 3 — Live market data

**Status:** Not started

Implement the Alpaca market-data adapter, historical baselines, stream health checks, reconnection, stale-data detection, and missing-bar backfill.

**Complete when:** a small watchlist can produce normalized market signals reliably during regular market hours.

### Milestone 4 — Live news and classification

**Status:** Not started

Add Alpaca news ingestion, deterministic filtering, duplicate handling, classifier-call limits, and a small structured AI classifier.

**Complete when:** relevant news can enrich or create events without classifying or researching every article.

### Milestone 5 — Research and reporting

**Status:** Not started

Build evidence packets, bounded read-only research tools, focused AI research, source tracking, and validated structured reports.

**Complete when:** a significant event produces a bounded, evidence-based report with citations, uncertainty, and a permitted research posture.

### Milestone 6 — Discord and operations

**Status:** Not started

Add idempotent Discord delivery, safe retries, structured operational status, and API rate and cost enforcement.

**Complete when:** one completed report produces one useful Discord alert and failures remain visible and recoverable.

### Milestone 7 — Full-loop hardening

**Status:** Not started

Replay representative market scenarios and failures through the same boundaries used in live operation. Tune rules only from observed behavior.

**Complete when:** the full MVP satisfies the success criteria in [`PRODUCT.md`](PRODUCT.md) and can run locally with documented limitations.

## Development Rules

- Build one thin vertical slice at a time.
- Complete and approve a feature spec before implementing a meaningful milestone.
- Add abstractions only at real replacement seams.
- Keep external integrations out until the prior internal flow is proven.
- Favor reliability, replayability, and cost control over additional features.
- Record changes to stable product or architecture choices in [`DECISIONS.md`](DECISIONS.md).

## After the MVP

Potential follow-up work—such as cloud deployment, a user interface, richer valuation tools, or additional providers—will be considered only after the local MVP works reliably. These are not current commitments.
