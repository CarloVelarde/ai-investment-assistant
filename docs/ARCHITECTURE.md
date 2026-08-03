# AI Investment Assistant — Architecture

**Status:** Approved

The MVP is one local, single-process Python application. It may use `asyncio` for concurrent I/O but has no microservices or distributed workers.

```mermaid
flowchart TD
    A["Market inputs"] --> B["Normalize and retain history"]
    B --> C["Fast detector"]
    B --> D["Daily trend detector"]
    N["News inputs"] --> O["Filter and classify"]
    C --> E["Normalized signals"]
    D --> E
    O --> E
    E --> F["Event manager"]
    F -->|Research eligible| G["Assemble evidence"]
    G --> H["Bounded research"]
    H --> I["Validate and persist"]
    I --> J["Notify when warranted"]
```

Product behavior is defined in [`PRODUCT.md`](PRODUCT.md), durable choices in [`DECISIONS.md`](DECISIONS.md), and implementation details in feature specs.

## Principles

- Deterministic code handles measurable rules, filtering, cooldowns, and deduplication.
- Market and significant news signals may qualify independently.
- Detectors emit signals; one event manager owns promotion and research eligibility.
- News classification is cheaper and separate from focused research.
- Provider objects are normalized before entering core logic.
- Research receives application-assembled evidence and narrow read-only tools.
- Related signals enrich one durable event instead of creating repeated work.
- Add abstractions only at demonstrated replacement seams.

## Boundaries

### Input adapters

Adapters retrieve market and news data and convert provider responses into validated internal records. Alpaca is the first live provider, but its SDK types must not enter core logic. Replay fixtures use the same downstream boundaries.

### Detection

Detection consumes normalized records and only produces signals; it does not enqueue research.

- The fast market detector evaluates completed bars for abrupt movement and emits only on a qualifying threshold crossing.
- The after-close daily market detector uses the same history to evaluate five- and twenty-trading-day movement, recent-high drawdown, and performance relative to `SPY`.
- News passes deterministic filters before a small structured classifier and may emit a significant news signal without a market signal.

There is no separate weekly pipeline. Exact thresholds, severity boundaries, and rearm rules remain feature-level decisions. Detection failures remain visible without crashing the application.

### Events

The event manager consumes all qualifying signals and alone owns correlation, promotion, deduplication, cooldowns, lifecycle state, retry eligibility, and research eligibility. A market or significant news signal may create an event by itself; later related signals enrich it.

Sustained directional movement is one evolving episode. Same-severity repeats are retained without repeated work. A worse severity, a newly crossed horizon, or significant new news may update and requeue the episode. Rejected inputs create no event or cooldown; notification cooldown begins only after successful delivery.

### Research

Research receives a prepared evidence packet only after an event qualifies. The application may expose bounded read-only access to market and news providers, SEC EDGAR, prior history, and hosted web search. The model receives no credentials or unrestricted database, filesystem, shell, or network access. Its output must match a validated schema.

### Persistence

SQLite stores the state needed for recovery, history, replay, and idempotency:

- Configuration and watchlist data.
- Normalized market history and detector baselines.
- Signals, articles, and classifications.
- Event lifecycle and retry state.
- Reports, source metadata, and provenance.
- Notification attempts and failures.

Writes pass through one controlled application boundary.

### Output

Validate and persist a report before delivery. Discord is the first live notification adapter. Retries are allowed, but the same report must not be sent twice.

## Core data

Exact fields and type names belong to feature specs, but these concepts are stable:

| Concept | Responsibility |
| --- | --- |
| Market record | Normalized price, volume, provider, feed, time, and completeness data |
| Market signal | Rule, horizon, direction, baseline, observed movement, severity, time, and provenance |
| News article | Normalized identity, content metadata, symbols, source, and timestamps |
| News classification | Relevance, category, direction, significance, confidence, and model metadata |
| News signal | A significant classified article eligible to create or enrich an event |
| Event | Independent or correlated signals, episode identity, severity, lifecycle, and deduplication state |
| Research input | Evidence packet and focused questions |
| Research report | Findings, evidence, competing explanations, uncertainty, confidence, and posture |

Retain provider, feed, source, retrieval time, and model or prompt version wherever they affect interpretation or replay.

## Event lifecycle

```mermaid
stateDiagram-v2
    [*] --> Collecting
    Collecting --> Rejected: Below threshold
    Collecting --> Queued: Significant
    Queued --> Researching
    Researching --> Reported: Success
    Researching --> Failed: Error or limit
    Reported --> Updated: Material evidence
    Updated --> Researching
```

Lifecycle state survives restarts. Routine same-severity updates do not reopen a reported event; escalation, a new horizon, or significant news may.

## Reliability and security

- Detect stale streams, reconnect, and backfill missing bars where possible.
- Persist event and notification state before irreversible actions.
- Use stable identifiers and idempotent processing.
- Retry transient failures with bounded backoff.
- Record invalid model output and unavailable research or delivery.
- Enforce classifier, research, tool, source-size, time, rate, and cost limits.
- Expose structured logs and basic health information.
- Load credentials from the environment; never place them in fixtures, logs, or prompts.
- Validate external input and model output at their boundaries.
- Provide no brokerage or order-execution capability.

## First slice

The completed [offline walking skeleton](../specs/001-offline-walking-skeleton/SPEC.md) proves the internal boundaries with one paired market-and-news scenario. That demonstration does not make news a gate for market events or market movement a gate for significant news. Later milestones add durable independent triggers, broader market detection, and live integrations in that order.
