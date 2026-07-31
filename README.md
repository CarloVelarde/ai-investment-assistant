# AI Investment Assistant

A local-first market monitoring and research assistant for a long-term investor.

The application watches a small stock list, detects potentially meaningful market or news events, correlates related signals, performs focused AI-assisted research, and sends one evidence-based report for review.

It does **not** execute trades or make final investment decisions.

## Core Flow

```text
Market and news data
  → deterministic detection and filtering
  → event correlation
  → bounded research
  → validated report
  → notification
```

The project favors deterministic rules before AI, provider-independent core logic, selective research, replayable scenarios, and duplicate-safe notifications.

## Current Status

The project is in **Milestone 0: Repository foundation**.

The first product slice will be an offline walking skeleton using fixtures and fake providers. Live APIs, SQLite, AI calls, and Discord will be added only in later milestones.

See the [roadmap](docs/ROADMAP.md) for the development sequence.

## Tech Stack

- Python 3.14
- `uv` for Python and dependency management
- Ruff for formatting and linting
- mypy for type checking
- pytest for testing
- SQLite, Alpaca, OpenAI, SEC EDGAR, and Discord in later milestones

## Local Setup

Install [`uv`](https://docs.astral.sh/uv/), then run:

```bash
uv sync
uv run ai-investment-assistant
```

No API keys are required during the repository-foundation or offline walking-skeleton milestones.

## Quality Checks

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
```

## Project Documentation

- [Product](docs/PRODUCT.md) — scope, behavior, constraints, and success criteria
- [Architecture](docs/ARCHITECTURE.md) — system flow, boundaries, and reliability model
- [Decision log](docs/DECISIONS.md) — durable accepted and rejected choices
- [Roadmap](docs/ROADMAP.md) — milestone order and current status
- [Agent instructions](AGENTS.md) — development workflow and repository rules

Feature specifications live under `specs/` as they are introduced.

## License

MIT
