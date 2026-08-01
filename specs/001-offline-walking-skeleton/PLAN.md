# Implementation Plan: Offline Walking Skeleton

**Document status:** Approved

## Approach

Build one small, synchronous pipeline that runs entirely from local fixtures and is invoked by the existing console entry point.

The implementation will prove the end-to-end flow before adding live providers, databases, background jobs, or real AI services.

```text
fake providers
→ normalization
→ detection
→ correlation
→ event
→ fake research
→ console notification
```

## Scenario rules

### Market rule

Create a market signal when:

- the symbol matches the tracked symbol;
- `latest_price <= previous_close * 0.95`; and
- `current_volume >= average_volume * 1.5`.

### News filter

Create a news signal when:

- the article's normalized symbol matches the tracked symbol; and
- a case-insensitive search of its headline and summary contains at least one fixed phrase below.

Fixed phrases for this slice:

- `guidance cut`
- `earnings miss`
- `investigation`
- `product recall`

### Correlation rule

Create an event when:

- one qualifying market signal and one qualifying news signal share the same normalized symbol; and
- the absolute difference between their timestamps is no more than 60 minutes, inclusive.

The event timestamp will come from the controllable clock.

## Components

### Fixture providers

- Read market and news records from local JSON files.
- Return fixture-shaped raw data.
- Perform no detection or correlation logic.

### Normalization

- Convert raw market fixture data into one internal market record.
- Convert raw news fixture data into one internal news record.
- Normalize symbols by trimming whitespace and converting them to uppercase.
- Parse timezone-aware ISO 8601 timestamps and convert them to UTC.
- Use the market-record timestamp and news publication timestamp as the signal occurrence times.
- Validate required fields and require positive prices and volume baselines and non-negative current volume.

### Detection

- Apply the single market rule.
- Apply the single news filter.
- Return a signal only when the relevant rule passes.

### Correlation

- Match qualifying signals by symbol.
- Check the 60-minute correlation window.
- Create exactly one significant event for the fixture scenario.

### Clock

- Provide the current time through a small clock dependency.
- Use a fixed clock in tests and fixture scenarios.
- Avoid direct dependence on the real system clock in core logic.

### Research

- Accept a created event.
- Return a fixed, clearly labeled fake research report.
- Run only when an event exists.

### Notification

- Format the event and fake research report as console output.
- Emit a distinct event-notification message through the existing logging foundation.
- Emit exactly one event notification for the triggering scenario.

### Runtime entry point

- Keep the existing `ai-investment-assistant` console command and configuration and logging bootstrap.
- Run the bundled triggering fixture pair with a fixed scenario time.
- Add no CLI framework or command-line options in this slice.

## Data flow

1. Load the market and news fixtures.
2. Normalize both raw records.
3. Evaluate the market rule.
4. Evaluate the news filter.
5. Correlate any resulting signals.
6. Stop when no event is created.
7. When an event exists, generate fake research.
8. Emit one console notification.

## Fixture scenarios

### Triggering scenario

The fixture data will contain:

- the same symbol in market and news records;
- a price decline of at least 5%;
- volume at or above 1.5 times average volume;
- one accepted negative-event phrase; and
- timestamps within 60 minutes.

Expected result:

- one event;
- one fake research report;
- one console notification.

### Non-triggering scenario

The market fixture will qualify, while the same-symbol news fixture will contain none of the fixed negative-event phrases. This proves that market movement alone does not create an event.

Expected result:

- no event;
- no research report;
- no event notification.

## Validation

Add focused tests for:

- market and news fixture loading and normalization;
- passing and failing market detection;
- passing and failing news filtering;
- successful correlation by normalized symbol;
- rejection for a different symbol and outside the correlation window;
- exactly one event for one qualifying signal pair;
- research gating and console notification count and content;
- the complete triggering pipeline;
- the complete non-triggering pipeline;
- fixed-clock behavior.

All tests must run without network access or external credentials.

## Risks and tradeoffs

- The chosen thresholds are demonstration rules, not investment advice.
- Phrase matching is intentionally simple and will produce limitations that later features can replace.
- The synchronous pipeline is sufficient for this milestone and should not be generalized into background workers yet.
- Internal models should be clear and typed, but abstractions should only be introduced where this feature directly needs them.

## Implementation completion

Implementation is complete when every acceptance criterion and task is complete and all repository checks pass.
