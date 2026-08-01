# AI Investment Assistant

A local market-monitoring and research assistant for a long-term investor. It detects meaningful market or news events, correlates related signals, performs focused research, and sends one evidence-based report for review. It never trades or makes final investment decisions.

```text
market and news data
  → deterministic detection
  → event correlation
  → bounded research
  → validated report
  → notification
```

See the [roadmap](docs/ROADMAP.md) for current status and milestone order.

## Setup

Requires Python 3.14 and [`uv`](https://docs.astral.sh/uv/):

```bash
uv sync
uv run ai-investment-assistant
```

## Checks

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
```

## Documentation

- [Product](docs/PRODUCT.md) — MVP scope and success criteria
- [Architecture](docs/ARCHITECTURE.md) — stable boundaries and data flow
- [Decisions](docs/DECISIONS.md) — durable choices and rejected alternatives
- [Roadmap](docs/ROADMAP.md) — milestone order and status
- [Agent instructions](AGENTS.md) — repository workflow and rules
- [Offline walking skeleton](specs/001-offline-walking-skeleton/SPEC.md) — active feature specification

## License

MIT
