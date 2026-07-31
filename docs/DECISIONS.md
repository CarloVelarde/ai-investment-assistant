# AI Investment Assistant — Decision Log

**Status:** Active  
**Last reviewed:** July 31, 2026

## Document Purpose

This file records durable product, architecture, tooling, and repository decisions that future work should not silently change.

Routine implementation details belong in code or the active feature spec. If a decision changes, add a new entry and mark the old one superseded rather than rewriting history.

## Accepted Decisions

### D-001 — Build a research assistant, not a trading system

**Status:** Accepted

The product monitors a small stock watchlist, investigates significant events, and presents evidence for user review. It will not execute trades or issue authoritative buy or sell decisions.

**Why:** The project serves a long-term investor and keeps financial decisions under human control.

### D-002 — Use one local Python application for the MVP

**Status:** Accepted

The MVP will run as one modular, single-process Python application. `asyncio` may coordinate concurrent I/O where useful.

**Why:** This is sufficient for a solo project and avoids unnecessary distributed-system complexity.

### D-003 — Keep external providers replaceable

**Status:** Accepted

Core logic will consume normalized internal models through narrow provider interfaces. Alpaca SDK types must not enter the detection or research core.

**Why:** Alpaca Basic may later be replaced by Algo Trader Plus or another provider without rewriting the core pipeline.

### D-004 — Use deterministic logic before AI

**Status:** Accepted

Normal code handles measurable rules, filtering, correlation, cooldowns, and duplicate detection. AI is used only where semantic judgment adds value.

**Why:** Deterministic behavior is cheaper, easier to test, and easier to explain.

### D-005 — Separate news classification from research

**Status:** Accepted

A small, inexpensive classifier decides whether news is relevant and significant. A separate, more capable research process runs only after an event passes the significance threshold.

**Why:** Continuous monitoring stays affordable while deeper analysis remains available for important events.

### D-006 — Correlate signals into durable events

**Status:** Accepted

Related price, volume, and news signals become one event with lifecycle state. Materially new evidence may update an event; routine repeated signals must not create duplicate research or notifications.

**Why:** The product should produce one useful report per real event.

### D-007 — Use bounded, application-controlled research

**Status:** Accepted

The application assembles evidence and exposes narrow, read-only research tools. Research output must follow a validated schema and remain subject to time, tool-call, source-size, rate, and cost limits.

**Why:** This improves safety, reliability, reproducibility, and cost control.

### D-008 — Use SQLite for MVP durability

**Status:** Accepted

SQLite will store event, report, failure, notification, and provenance state. Concurrent writes will pass through a controlled application boundary.

**Why:** SQLite provides sufficient durability without requiring a separate database service. The exact schema and access library remain flexible.

### D-009 — Use Alpaca, SEC EDGAR, OpenAI, and Discord initially

**Status:** Accepted

- Alpaca is the initial market-data and news provider.
- SEC EDGAR and hosted web search provide additional research evidence.
- OpenAI provides structured classification and research capabilities.
- Discord is the initial notification channel.

**Why:** Together they support the complete MVP loop while remaining practical for a personal project.

### D-010 — Build the offline walking skeleton first

**Status:** Accepted

The first feature will prove this real internal flow:

> Fixtures → normalization → deterministic detection → signal correlation → event → fake research report → console notification

It will use no internet, secrets, database, live provider, LLM, or Discord integration.

**Why:** This validates the core boundaries before external-service complexity is introduced.

### D-011 — Standardize the Python foundation

**Status:** Accepted

- Repository: `ai-investment-assistant`
- Import package: `investment_assistant`
- Python: CPython `>=3.14,<3.15`, managed by `uv`
- Layout: `src/investment_assistant/`
- Build backend: `uv_build`
- Quality tools: Ruff, mypy, and pytest
- Configuration: Pydantic and `pydantic-settings`
- CI: GitHub Actions on Ubuntu
- Lockfile: commit `uv.lock`

**Why:** This gives the project a modern, reproducible, and lightweight development setup.

### D-012 — Use WSL2 Ubuntu as the primary development environment

**Status:** Accepted

Development will occur in WSL2 Ubuntu, with the repository stored in the Linux filesystem rather than under `/mnt/c/`. Docker will be introduced after the local foundation works.

**Why:** This provides a consistent Linux-oriented environment with better filesystem behavior for the selected tools.

### D-013 — Keep the repository public and MIT licensed

**Status:** Accepted

The GitHub repository will be public, use `main` as its default branch, and use the MIT License.

**Why:** The project is intended to be resume-visible and easy for others to inspect.

### D-014 — Use focused permanent docs and feature specs

**Status:** Accepted

Permanent truth is divided among `README.md`, `AGENTS.md`, `docs/PRODUCT.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, and `docs/ROADMAP.md`. Each meaningful feature uses `SPEC.md`, `PLAN.md`, and `TASKS.md` under a numbered `specs/` folder.

**Why:** Agents and humans should load only the context relevant to the current task.

## Rejected Decisions

### D-015 — Create a separate `RULES.md`

**Status:** Rejected

Repository and agent rules belong in `AGENTS.md`. A second rules file would duplicate authority and create drift.

### D-016 — Pre-build the full package and service hierarchy

**Status:** Rejected

Packages, abstractions, and services will be introduced when an active vertical slice requires them. Empty scaffolding would imply certainty the project does not yet have.

### D-017 — Use distributed or enterprise infrastructure for the MVP

**Status:** Rejected

The MVP will not use microservices, Redis, Celery, Kafka, Kubernetes, or a complex multi-agent framework.

## Intentionally Deferred

These choices will be made in the relevant feature spec when implementation provides enough evidence:

- Exact package and class structure.
- Database schema, repository methods, and SQLite access library.
- Detection thresholds, correlation windows, and scoring formulas.
- Number and arrangement of asynchronous workers and queues.
- Exact model selections, prompts, and report wording.
- Optional libraries not required by the active feature.
- Cloud deployment, web or mobile interfaces, and multi-provider failover.
