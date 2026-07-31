# Implementation Plan: Offline Walking Skeleton

**Status:** Draft

## Approach

Build one small, synchronous pipeline that runs entirely from local fixtures.

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
- the latest price is at least 5% below the previous close; and
- current volume is at least 1.5 times average volume.

### News filter

Create a news signal when:

- the article references the tracked symbol;
- the article timestamp is valid; and
- the normalized headline or summary contains at least one configured negative-event phrase.

Initial phrases:

- `guidance cut`
- `earnings miss`
- `investigation`
- `product recall`

### Correlation rule

Create an event when:

- one qualifying market signal and one qualifying news signal share the same symbol; and
- their timestamps are no more than 60 minutes apart.

The event timestamp will come from the controllable clock.

## Components

### Fixture providers

- Read market and news records from local JSON files.
- Return provider-shaped raw data.
- Perform no detection or correlation logic.

### Normalization

- Convert raw market fixture data into one internal market record.
- Convert raw news fixture data into one internal news record.
- Validate required fields and timestamps.

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
- Print exactly one event notification for the triggering scenario.

## Data flow

1. Load the market and news fixtures.
2. Normalize both raw records.
3. Evaluate the market rule.
4. Evaluate the news filter.
5. Correlate any resulting signals.
6. Stop when no event is created.
7. When an event exists, generate fake research.
8. Print one console notification.

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

The fixture data will fail at least one required condition.

Prefer using qualifying market data with unrelated or non-qualifying news so the scenario proves that market movement alone does not create an event.

Expected result:

- no event;
- no research report;
- no event notification.

## Validation

Add focused tests for:

- market fixture normalization;
- news fixture normalization;
- passing and failing market detection;
- passing and failing news filtering;
- successful correlation;
- rejection outside the correlation window;
- the complete triggering pipeline;
- the complete non-triggering pipeline;
- fixed-clock behavior;
- console output count and content.

All tests must run without network access or external credentials.

## Risks and tradeoffs

- The chosen thresholds are demonstration rules, not investment advice.
- Phrase matching is intentionally simple and will produce limitations that later features can replace.
- The synchronous pipeline is sufficient for this milestone and should not be generalized into background workers yet.
- Internal models should be clear and typed, but abstractions should only be introduced where this feature directly needs them.

## Completion

The plan is complete when:

- the feature can be divided into small ordered tasks;
- every acceptance criterion maps to an implementation step or test; and
- no external integration is required.
