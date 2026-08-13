# AI Investment Assistant

A local market-monitoring and research assistant. It watches a small list of US stocks, notices meaningful price moves or news, investigates selected situations, and sends one focused report for review.

It does not trade, promise certainty, or make investment decisions for you.

## How it works

```mermaid
flowchart LR
    I["Market and news"] --> N["Notice what matters"]
    N -->|"not noteworthy"| X["Ignore"]
    N -->|"noteworthy"| E["Keep one case file"]
    E -->|"routine update"| S["Save quietly"]
    E -->|"worth a closer look"| R["Research"]
    R --> P["Save a report"]
    P --> O["Notify user"]
```

Related updates stay on the same case so you are not flooded with repeats. Significant good news and bad news can both start a case. Details live in the [architecture](docs/ARCHITECTURE.md).

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

Further product, design, and project notes are in [`docs/`](docs/).

## License

MIT
