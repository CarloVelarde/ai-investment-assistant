# Implementation Plan: Offline Walking Skeleton

**Document status:** Approved

## Approach

Build one synchronous pipeline over local fixture readers and invoke it from the existing console entry point. The pipeline receives a tracked symbol, market and news fixture paths, and a current-time dependency.

## Rules

### Market signal

The normalized symbol must match the tracked symbol, `latest_price <= previous_close * 0.95`, and `current_volume >= average_volume * 1.5`. Thresholds are inclusive.

### News signal

The normalized symbol must match the tracked symbol. A case-insensitive substring search across headline and summary must find one fixed phrase:

- `guidance cut`
- `earnings miss`
- `investigation`
- `product recall`

### Correlation

The market and news signals must share a normalized symbol, and the absolute difference between their occurrence times must be at most 60 minutes, inclusive. The injected clock supplies the event time.

## Design

1. Fixture readers load raw market and news JSON without detection logic.
2. Normalization trims and uppercases symbols, converts timezone-aware ISO 8601 timestamps to UTC, and validates required fields. Prices and average volume must be positive; current volume must be non-negative.
3. Pure detection functions produce market and news signals. Market-record time and news publication time become signal occurrence times.
4. Correlation produces one event or no event.
5. Fake research accepts only an event and returns a fixed structured summary prefixed `FAKE RESEARCH — NOT INVESTMENT ANALYSIS`.
6. The notifier emits one log message beginning `EVENT NOTIFICATION` and containing the event and report.
7. The pipeline stops before research and notification when no event exists.

Inject current time rather than reading the system clock in core logic. Keep the existing `ai-investment-assistant` command, configuration, and logging bootstrap; it runs the bundled triggering fixtures with a fixed scenario time. Add no CLI framework or options.

## Fixtures

| Scenario | Inputs | Result |
| --- | --- | --- |
| Triggering | Same symbol, both rules pass, timestamps within 60 minutes | One event, report, and notification |
| Non-triggering | Market passes; same-symbol news contains no fixed phrase | No event, research, or notification |

## Validation

- Fixture loading, validation, and normalization.
- Passing, failing, and inclusive market-rule boundaries.
- News matches in headline and summary, including case differences.
- Correlation for normalized symbols and times inside, at, and outside 60 minutes.
- Exactly one event for one qualifying pair.
- Research gating and notification count and content.
- Triggering and non-triggering pipelines with a fixed clock.
- No network or credentials.

## Tradeoffs

- Thresholds and phrase matching demonstrate the flow; they are not investment advice.
- The synchronous design is sufficient for this milestone.
- Add only types and seams exercised by this slice.
