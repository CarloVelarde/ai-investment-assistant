# AI Investment Assistant — Agent Instructions

Keep changes small, explainable, and aligned with the active milestone.

## Communication

Prefer plain language a careful high-school reader can follow. Use technical terms when they add clarity, but explain ideas in everyday words first. Keep summaries, questions, tradeoffs, and status updates short and concrete. Avoid jargon-heavy prose unless the user asks for deep technical detail.

## Required context

Read only:

1. This file.
2. The active feature's `SPEC.md`, `PLAN.md`, and `TASKS.md`.
3. The permanent document that owns the affected decision:
   - [`docs/PRODUCT.md`](docs/PRODUCT.md) for product behavior and scope.
   - [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for system boundaries and data flow.
   - [`docs/DECISIONS.md`](docs/DECISIONS.md) for durable choices.
   - [`docs/ROADMAP.md`](docs/ROADMAP.md) for milestone order and status.

`docs/archive/` is not authoritative. Do not load every document by default.

## Workflow

1. Confirm the task belongs to the active milestone and approved spec.
2. Inspect existing code and tests.
3. Implement the smallest complete behavior that meets the acceptance criteria.
4. Add tests with the implementation.
5. Update `TASKS.md` as status changes.
6. Run all repository checks.
7. Summarize changes, validation, and limitations.

Documentation, tooling, and corrective changes may proceed without a feature spec when they do not change product scope or stable architecture.

## Implementation rules

- Use Python `>=3.14,<3.15` and `uv`.
- Keep code under `src/investment_assistant/` and tests under `tests/`.
- Prefer typed, explicit code and thin vertical slices.
- Add interfaces only at real external or replaceable boundaries.
- Convert provider objects into internal models before core logic.
- Keep deterministic detection, filtering, correlation, cooldowns, and deduplication outside AI components.
- Let market and significant news signals qualify independently; neither gates the other.
- Keep fast and daily market evaluation in one pipeline with one signal contract.
- Detectors emit signals; only the event manager decides promotion and research eligibility.
- Keep news classification separate from research.
- Make time and external I/O controllable when behavior depends on them.
- Preserve provenance when source, feed, retrieval time, or model version affects interpretation.
- Introduce external services only in their roadmap milestone.

Do not pre-create unused packages, services, abstractions, or optional dependencies.

## Validation

Before completing a code task, run:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
```

Tests must be deterministic, avoid live services, and cover public behavior. Use fakes for providers, clocks, research, and notifications. Add regression tests for reproducible bugs. Never weaken checks to make them pass; report any check that cannot run.

## Documentation ownership

- Product scope and behavior: `docs/PRODUCT.md`
- Stable architecture: `docs/ARCHITECTURE.md`
- Durable or superseded decisions: `docs/DECISIONS.md`
- Milestone status: `docs/ROADMAP.md`
- Feature behavior and acceptance criteria: `SPEC.md`
- Implementation approach: `PLAN.md`
- Execution status: `TASKS.md`

Record changes to stable product or architecture decisions in `docs/DECISIONS.md` first.

## Safety and scope

- Never commit credentials, `.env` files, tokens, or private financial data.
- Keep secrets out of fixtures, logs, exceptions, prompts, and test output.
- Validate external inputs and structured model outputs at their boundaries.
- Keep research tools narrow, read-only, bounded, and application-controlled.
- Never add brokerage integration, order execution, or autonomous trading.
- Preserve unrelated changes; do not commit or modify remotes unless asked.
- Favor reliability, replayability, and a working core loop over infrastructure breadth.
- Do not add microservices, Redis, Celery, Kafka, Kubernetes, or a complex multi-agent framework.
