# Feature Specification: Offline Walking Skeleton

**Status:** Draft

## Purpose

Create the first end-to-end vertical slice of the investment assistant without external services.

The feature proves that market and news inputs can move through the full pipeline:

```text
JSON fixtures
→ normalization
→ deterministic detection
→ signal correlation
→ event
→ fake research report
→ console notification
```

## Scope

### In scope

- Read market and news data from local JSON fixtures.
- Normalize provider-specific fixture data into internal market and news records.
- Evaluate one deterministic market rule.
- Evaluate one deterministic news filter.
- Correlate related market and news signals.
- Create a significant event when the required signals match.
- Use a controllable clock so scenarios are deterministic.
- Generate a fake research report for a created event.
- Print a console notification containing the event and research result.
- Support one triggering scenario.
- Support one non-triggering scenario.

### Out of scope

- Internet access or live data.
- API keys or secrets.
- Alpaca or another real market-data provider.
- OpenAI or another LLM.
- SEC EDGAR.
- Discord or another external notification service.
- SQLite or another database.
- Background scheduling or continuous monitoring.
- Deployment or cloud infrastructure.
- Multiple detection rules or configurable thresholds.
- A production-ready command-line interface.

## Behavior

### Triggering scenario

Given local market and news fixtures for the same tracked symbol:

1. The market fixture satisfies the deterministic market rule.
2. The news fixture satisfies the deterministic news filter.
3. The signals occur within the allowed correlation window.
4. The system creates one significant event.
5. Fake research produces one report for that event.
6. The system prints one console notification.

### Non-triggering scenario

Given local fixtures where the required signals do not both qualify or do not correlate:

1. The system does not create a significant event.
2. Fake research is not run.
3. No event notification is printed.

Diagnostic or summary output may still be printed if it clearly indicates that no event was created.

## Inputs

- A local JSON market-data fixture.
- A local JSON news-data fixture.
- A fixed or controllable current time.
- The symbol represented by the fixture scenario.

## Outputs

For a triggering scenario:

- One normalized market record.
- One normalized news record.
- One correlated significant event.
- One fake research report.
- One console notification.

For a non-triggering scenario:

- No significant event.
- No research report.
- No event notification.

## Acceptance criteria

- [ ] The complete pipeline runs locally without internet access.
- [ ] Market data is loaded from a JSON fixture and normalized.
- [ ] News data is loaded from a JSON fixture and normalized.
- [ ] One deterministic market rule can produce a market signal.
- [ ] One deterministic news filter can produce a news signal.
- [ ] Related qualifying signals can be correlated by symbol and time.
- [ ] A correlated signal pair creates exactly one significant event.
- [ ] Fake research runs only after an event is created.
- [ ] A triggering fixture scenario prints exactly one event notification.
- [ ] A non-triggering fixture scenario creates no event and runs no research.
- [ ] Tests use a controllable clock and do not depend on the real current time.
- [ ] The feature requires no external service, API key, database, or network connection.
- [ ] Ruff, mypy, and pytest pass once project tooling is configured.

## Constraints

- Keep the implementation small and synchronous.
- Prefer explicit code over premature abstractions.
- Add only the modules and interfaces required by this vertical slice.
- Fake providers must remain replaceable later without coupling detection logic to fixture formats.
- Detection and correlation behavior must be deterministic.
- The fake research report must be clearly identified as generated test output, not real investment analysis.
- This feature must not make or execute investment decisions.

## Open questions

- The exact market-rule threshold, news-filter condition, and correlation window will be selected in `PLAN.md`.
