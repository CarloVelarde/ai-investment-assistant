# AI Investment Assistant — Agent Instructions

## Purpose

This file defines how coding agents should work in this repository. Keep changes small, explainable, and aligned with the active milestone.

## Required Context

Read only the context relevant to the task:

1. This file.
2. The active feature's `SPEC.md`, `PLAN.md`, and `TASKS.md`, if one exists.
3. The permanent document that owns the affected decision:
   - [`docs/PRODUCT.md`](docs/PRODUCT.md) for product scope and behavior.
   - [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for system boundaries and data flow.
   - [`docs/DECISIONS.md`](docs/DECISIONS.md) for durable accepted and rejected choices.
   - [`docs/ROADMAP.md`](docs/ROADMAP.md) for milestone order and status.

Do not treat `docs/archive/` as authoritative. Do not load every document by default.

## Working Process

1. Confirm the task belongs to the active milestone and spec.
2. Inspect the existing code and tests before proposing changes.
3. Implement the smallest complete behavior that satisfies the acceptance criteria.
4. Add or update tests with the implementation.
5. Update the active `TASKS.md` when task status changes.
6. Run all repository checks before declaring completion.
7. Summarize what changed, what was validated, and any remaining limitations.

Meaningful features require an approved spec. Small documentation, tooling, or corrective changes may proceed without a new spec when they do not change product scope or stable architecture.

## Implementation Rules

- Use Python `>=3.14,<3.15` and manage dependencies with `uv`.
- Keep application code under `src/investment_assistant/` and tests under `tests/`.
- Prefer typed, straightforward code over clever or overly generic designs.
- Build thin vertical slices; do not pre-create unused packages, services, or abstractions.
- Add interfaces only at real external or replaceable boundaries.
- Convert provider SDK objects into internal models before they enter core logic.
- Keep deterministic detection, filtering, correlation, cooldowns, and deduplication outside AI components.
- Keep the cheap news classifier separate from focused research.
- Make time and external I/O controllable when behavior depends on them.
- Preserve provenance where data source, feed, retrieval time, or model version affects interpretation.

Do not introduce external services before their roadmap milestone. In particular, the offline walking skeleton must not use live APIs, secrets, SQLite, LLM calls, Alpaca, SEC EDGAR, or Discord.

## Testing and Validation

Before completing a code task, run:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
```

Testing expectations:

- Tests must be deterministic and must not depend on live external services.
- Test behavior and public boundaries rather than private implementation details.
- Use fixtures and fakes for providers, clocks, research, and notifications where appropriate.
- Add a regression test when fixing a reproducible bug.
- Do not weaken checks or remove tests merely to make validation pass.

If a check cannot run or pass, report the exact reason rather than claiming completion.

## Documentation Ownership

Keep each fact in one authoritative location and link to it elsewhere.

- Product scope changes belong in `docs/PRODUCT.md`.
- Stable architecture changes belong in `docs/ARCHITECTURE.md`.
- Durable decisions and superseded choices belong in `docs/DECISIONS.md`.
- Milestone status belongs in `docs/ROADMAP.md`.
- Feature behavior and acceptance criteria belong in its `SPEC.md`.
- Implementation approach belongs in its `PLAN.md`.
- Execution status belongs in its `TASKS.md`.

Do not silently change stable product or architecture decisions. Record and explain the change in `docs/DECISIONS.md` first.

## Security and Safety

- Never commit credentials, `.env` files, tokens, or private financial data.
- Never place secrets in fixtures, logs, exceptions, model prompts, or test output.
- Validate external inputs and structured model outputs at their boundaries.
- Keep research tools narrow, read-only, bounded, and application-controlled.
- Do not add brokerage integration, order execution, or autonomous trading behavior.
- Preserve unrelated user changes and avoid destructive Git operations.
- Do not commit, push, or modify remote resources unless explicitly requested.

## Scope Guardrails

This is a solo, resume-oriented project—not an enterprise platform. Favor reliability, replayability, understandable design, and a working core loop over infrastructure breadth.

Do not add microservices, Redis, Celery, Kafka, Kubernetes, a complex multi-agent framework, or optional libraries without a demonstrated need in the active feature.
