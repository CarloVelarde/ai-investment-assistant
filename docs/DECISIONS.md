# AI Investment Assistant — Decisions

**Status:** Active

This log owns durable choices. Routine details belong in code or the active feature spec. Accepted entries remain active unless a later entry marks them superseded.

## Accepted

### D-001 — Build a research assistant, not a trading system

Investigate significant events and present evidence for human review. Never execute trades or issue authoritative buy or sell decisions.

**Why:** Investment decisions remain under user control.

### D-002 — Use one local Python application for the MVP

Run one modular, single-process application; use `asyncio` only where concurrent I/O helps.

**Why:** A solo project does not need distributed-system complexity.

### D-003 — Keep external providers replaceable

Core logic consumes normalized internal models through narrow provider boundaries; provider SDK objects do not enter the core.

**Why:** Providers can change without rewriting detection or research.

### D-004 — Use deterministic logic before AI

Ordinary code handles measurable rules, filtering, correlation, cooldowns, and deduplication. AI handles semantic judgment.

**Why:** Deterministic behavior is cheaper, testable, and explainable.

### D-005 — Separate news classification from research

A small classifier triages news; capable research runs only after an event qualifies.

**Why:** Continuous monitoring stays affordable without weakening focused research.

### D-006 — Correlate signals into durable events

Related signals enrich one lifecycle-managed event. Routine repeats do not create duplicate research or notifications; materially new evidence may update an event.

**Why:** One real event should produce one useful report.

### D-007 — Use bounded, application-controlled research

The application assembles evidence and exposes narrow read-only tools. Research output follows a validated schema and time, tool, source-size, rate, and cost limits.

**Why:** Bounds improve safety, reliability, replayability, and cost control.

### D-008 — Use SQLite for MVP durability

SQLite stores event, report, failure, notification, and provenance state behind one controlled write boundary.

**Why:** It provides enough local durability without a database service.

### D-009 — Use Alpaca, SEC EDGAR, OpenAI, and Discord first

Alpaca supplies market data and news; SEC EDGAR and hosted search supply evidence; OpenAI supports classification and research; Discord delivers alerts.

**Why:** These services cover the MVP loop and remain practical for a personal project.

### D-010 — Build the offline walking skeleton first

Prove fixtures → normalization → deterministic detection → correlation → event → fake structured research → console notification before adding live services or SQLite.

**Why:** Validate the internal boundaries before integration complexity.

### D-011 — Standardize the Python foundation

- Repository: `ai-investment-assistant`
- Package: `investment_assistant` under `src/`
- Python: CPython `>=3.14,<3.15`, managed by `uv`
- Build: `uv_build`; commit `uv.lock`
- Runtime configuration: Pydantic and `pydantic-settings`
- Quality: Ruff, mypy, pytest, and GitHub Actions on Ubuntu

**Why:** The setup is modern, reproducible, and small.

### D-012 — Use WSL2 Ubuntu for primary development

Develop in WSL2 Ubuntu and keep the repository in its Linux filesystem rather than under `/mnt/c/`.

**Why:** This gives the selected tools consistent Linux filesystem behavior.

### D-013 — Keep the repository public and MIT licensed

Use a public GitHub repository, `main` as the default branch, and the MIT License.

**Why:** The project is resume-visible and easy to inspect.

### D-014 — Use focused permanent docs and feature specs

Permanent truth lives in `README.md`, `AGENTS.md`, and `docs/`. Each meaningful feature uses `SPEC.md`, `PLAN.md`, and `TASKS.md` in a numbered `specs/` directory.

**Why:** Humans and agents should load only relevant context.

### D-018 — Route independent signals through one event manager

Market and significant news signals may each qualify an event without the other. Detectors emit normalized signals but never enqueue research directly. One event manager owns correlation, promotion, deduplication, escalation, cooldowns, and research eligibility.

**Why:** Independent triggers avoid missed market-only or news-only events; one promotion owner prevents duplicate and inconsistent work.

### D-019 — Use fast and daily market evaluation in one pipeline

One fast detector evaluates completed market bars for abrupt movement. One fixed after-close daily scan evaluates five- and twenty-trading-day movement, recent-high drawdown, and broad-market-relative performance. Both use the same normalized history, signal contract, and event manager; there is no separate weekly service or user-configurable cadence initially.

**Why:** Two cadences catch abrupt and gradual movement without creating two systems or unnecessary scheduling options.

### D-020 — Let material escalation bypass suppression

This refines D-006: sustained movement belongs to one evolving episode, but a material update may justify a new report. Repeated evidence at the same severity is recorded without repeated research or notification; a worse severity, a newly crossed horizon, or significant new news may update and requeue the episode. Rejected inputs never start a cooldown, and notification cooldown begins only after successful delivery.

**Why:** Suppression should reduce noise without hiding meaningful deterioration or later explanations.

## Rejected

### D-015 — Add a separate `RULES.md`

Repository and agent rules stay in `AGENTS.md`; a second file would drift.

### D-016 — Pre-build the package and service hierarchy

Add packages, services, and abstractions only when an active vertical slice requires them.

### D-017 — Use enterprise infrastructure for the MVP

Do not add microservices, Redis, Celery, Kafka, Kubernetes, or a complex multi-agent framework.

## Deferred

Decide these in the feature that first needs them:

- Package and class structure.
- Database schema, repository methods, and SQLite library.
- Detection thresholds, correlation windows, severity boundaries, and rearm rules.
- Async worker and queue arrangement.
- Model selection, prompts, and report wording.
- Optional libraries, deployment, interfaces, and provider failover.
