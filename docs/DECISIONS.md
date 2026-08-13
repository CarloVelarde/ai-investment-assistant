# AI Investment Assistant — Decisions

**Status:** Active

This log owns durable choices. Routine details belong in code or the active feature spec. Accepted entries remain active unless a later entry marks them superseded.

## Accepted

### D-001 — Build a research assistant, not a trading system

Investigate significant events and present evidence for human review. Never execute trades or issue authoritative buy or sell decisions.

**Why:** Investment decisions remain under user control.

### D-023 — Do not treat one investor style as the product

The MVP is for one local user and a small US watchlist. That does **not** require a multi-year thesis, buy-and-hold-only behavior, or one reason for watching each name.

A lasting thesis with opportunistic review (buy a discount, consider taking profit after significant news, buy again later) is an in-scope example, not the only user. Other in-scope examples include watching names not yet owned, mixed holding periods on the same list, and active watching without day trading.

Out of scope remain day trading, scalping, high-frequency strategies, and any autonomous buy or sell.

Future detection, classification, research, and notification work must not assume “always hold,” require a long thesis, hide upside or good-news cases, or treat opportunistic review as out of product scope. The assistant still only supports review; the user decides whether to buy, sell, hold, or wait.

**Why:** “Long-term investor” was easy to misread as both buy-and-hold-only and the only persona. The product notices meaningful situations; it does not prescribe one strategy.

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

A small classifier triages news; capable research runs only after an event needs work. D-022 records that significance is independent of direction and that the classifier stays on the news path.

**Why:** Continuous monitoring stays affordable without weakening focused research.

### D-006 — Correlate signals into durable events

Related signals enrich one lifecycle-managed event. Routine repeats do not create duplicate research or notifications; materially new evidence may update an event.

**Why:** One real event should produce one useful report.

### D-007 — Use bounded, application-controlled research

The application assembles evidence and exposes narrow read-only tools. Research output follows a validated schema and time, tool, source-size, rate, and cost limits.

**Why:** Bounds improve safety, reliability, replayability, and cost control.

### D-008 — Use SQLite for MVP durability

SQLite, accessed through Python's built-in `sqlite3` module, stores event, report, failure, notification, and provenance state behind one controlled write boundary. Schema and repository method details remain feature-level decisions.

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

### D-021 — Offline market history uses fixed dual detectors and open episodes

Milestone 3 fixes offline market evaluation as follows:

- Persist normalized completed bars for watchlist symbols and `SPY`; evaluate only completed bars.
- One fast detector (`abrupt_move` / one-hour) and one after-close daily detector (`multi_day_move`, `drawdown_from_high`, `relative_to_spy`) share the Milestone 2 signal contract and event manager.
- Thresholds, importance steps, volume dampening for the fast rule, crossing, and rearm (rearm line = half the `MODERATE` magnitude) are defined in the Milestone 3 feature spec, not left open.
- Detector baseline state is durable so replay does not re-fire settled crossings.
- Market events track `episode_open`; grouping attaches same-direction market signals only to open episodes. An episode closes when all detector keys for that ticker and direction are clear/armed after daily evaluation. Close does not research by itself; a later new breach creates a new event.

**Why:** Agents and humans need explicit, testable market rules; forever-open episodes would merge unrelated later moves into old stories.

### D-022 — Treat news significance as direction-agnostic; keep the cheap classifier on the news path

This refines D-005 and D-018:

- A news article may be significant whether classification labels it positive (`UP`), negative (`DOWN`), or unclear. Earnings beats, expansions, and acquisitions are in scope if they pass significance; they are not discarded for being good news.
- The inexpensive structured classifier is **news-path triage only**. It helps decide whether an article becomes a news signal. It does not judge market-rule signals and does not decide research or notification.
- The event manager still decides whether a new or updated event needs work (research and, if that succeeds, notification).
- Offline demo detection that matches only a few negative phrases is a temporary Milestone 1/2 fixture rule, not the product news policy. Milestone 5 replaces that rule.

**Why:** Cost control belongs on the news firehose, not by ignoring positive stories. A second AI judge in front of every event would blur responsibilities and still miss market-only cases.

## Rejected

### D-015 — Add a separate `RULES.md`

Repository and agent rules stay in `AGENTS.md`; a second file would drift.

### D-016 — Pre-build the package and service hierarchy

Add packages, services, and abstractions only when an active vertical slice requires them.

### D-017 — Use enterprise infrastructure for the MVP

Do not add microservices, Redis, Celery, Kafka, Kubernetes, or a complex multi-agent framework.

## Deferred

Decide these in the feature that first needs them:

- Package and class structure beyond what active milestones already introduced.
- User-configurable detection thresholds and correlation windows (Milestone 3 ships fixed defaults; configuration can come later).
- Async worker and queue arrangement.
- Model selection, prompts, and report wording. The split is fixed: inexpensive news classification vs later focused research (D-005, D-022). Exact model names stay feature-level.
- Optional libraries, deployment, interfaces, and provider failover.
- Strong live-delivery claim/recovery (for example mark delivery in progress before an external send, and reconcile “sent but not recorded”) when Discord and production notification land (Milestone 7). Milestone 2 only re-checks current update before notify and honors a refused notify save.

Resolved in feature specs / accepted decisions above when applicable:

- Detection thresholds, severity, rearm, and market episode open/close → D-021 and Milestone 3 spec.
