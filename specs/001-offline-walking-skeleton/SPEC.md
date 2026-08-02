# Feature Specification: Offline Walking Skeleton

**Document status:** Approved

## Purpose

Prove the first complete pipeline with local fixtures and no external services.

## Scope

### In scope

- Load and normalize local market and news JSON fixtures.
- Apply one deterministic market rule and one deterministic news filter.
- Correlate qualifying signals into one event.
- Use a controllable clock.
- Produce a clearly labeled fake structured research report only for an event.
- Emit one console notification with the event and report.
- Provide one triggering and one non-triggering scenario.

### Out of scope

- Live data, internet access, secrets, external APIs, LLMs, or Discord.
- SQLite or other persistence.
- Scheduling, continuous monitoring, deployment, or cloud infrastructure.
- Multiple or configurable rules and a production CLI.

## Behavior

The triggering fixture pair qualifies under both rules and falls within the correlation window. It produces one event, one fake report, and one event notification.

The non-triggering pair does not produce both required signals. It produces no event, research, or event notification; diagnostic output may state that no event was created.

## Acceptance criteria

- [x] AC-01: The complete pipeline runs through the existing console entry point without internet access.
- [x] AC-02: Market and news JSON fixtures load into normalized internal records.
- [x] AC-03: The single market rule and news filter produce their respective signals when their conditions pass.
- [x] AC-04: One qualifying same-symbol pair within the time window creates exactly one event.
- [x] AC-05: Fake structured research runs only after event creation.
- [x] AC-06: The triggering scenario emits exactly one event notification containing the event and fake report.
- [x] AC-07: The non-triggering scenario creates no event, runs no research, and emits no event notification.
- [x] AC-08: Tests control time and never depend on the real clock.
- [x] AC-09: The feature adds no external service, secret, database, or network dependency.
- [x] AC-10: Ruff formatting and linting, mypy, and pytest pass.

## Constraints

- Keep the pipeline small, synchronous, typed, and deterministic.
- Keep fixture parsing outside core logic.
- Add no general provider or service hierarchy.
- Clearly label fake research as test output, not investment analysis.
- Never make or execute an investment decision.
