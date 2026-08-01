# AI Investment Assistant — Product

**Status:** Approved MVP

AI Investment Assistant is a local market-monitoring and research tool for one long-term investor. It watches a small list of US stocks, detects meaningful market or news events, investigates selected events, and sends one focused Discord report for review.

It does not trade, promise certainty, or make final investment decisions. The user remains responsible for every decision.

System boundaries are defined in [`ARCHITECTURE.md`](ARCHITECTURE.md); milestone order is in [`ROADMAP.md`](ROADMAP.md); feature behavior belongs under [`specs/`](../specs/).

## Core loop and principles

> Detect a meaningful event → correlate related signals → assemble evidence → produce one focused report → notify once.

- Use ordinary code for measurable rules, filtering, correlation, cooldowns, and deduplication.
- Use AI only for narrow news classification and focused research after escalation.
- Keep classification inexpensive and separate from research.
- Distinguish evidence from inference, cite important sources, and state uncertainty.
- Prefer recovery, replay, duplicate prevention, and cost control over more indicators or agents.
- Support review; never issue authoritative buy or sell instructions.

## MVP scope

### Configuration

The user can configure:

- A small watchlist and whether each stock is owned or watched.
- Optional cost basis and personal notes.
- Detection thresholds, correlation windows, cooldowns, and market-hours behavior.
- Notification preferences and API rate and cost limits.

The application has no brokerage connection.

### Market monitoring

Monitor minute-level activity for the watchlist and a few comparison symbols. Detection may use:

- Price movement over configured windows.
- Movement relative to the broad market, initially `SPY`.
- Same-feed volume and simple recent volatility context.

Detect stale or interrupted data and recover missing minute bars when possible.

### News monitoring

Filter company news by watchlist relevance, recency, source, event category, duplicates, and classifier-call limits. Qualifying articles receive a small structured classification with relevance, category, likely significance, direction, confidence, and rationale.

### Event correlation

Group related signals into one durable event rather than separate research jobs. The MVP must handle:

- Market movement with or without related news.
- Significant news before a price reaction.
- Broad-market or sector movement.
- Duplicate articles and materially new evidence.

Events retain enough lifecycle state for deduplication, cooldowns, retries, and restart-safe processing.

### Research

Research runs only for a significant event. It determines what happened, the strongest and competing explanations, whether the event is company-specific or broader, whether evidence may be fundamental, and what remains uncertain.

Research is bounded by time, tool calls, source size, rate, and cost.

### Report and notification

Each research run produces a validated report with:

- Ticker, company, trigger time, and triggering signals.
- Event summary and likely and competing explanations.
- Relevant price, volume, market, and sector context.
- Supporting sources, bullish and bearish considerations, and missing information.
- Confidence, uncertainty, and a cautious research posture.

Allowed postures are:

- `MONITOR`
- `INVESTIGATE_FURTHER`
- `POTENTIAL_OPPORTUNITY_TO_REVIEW`
- `WAIT_FOR_CLARITY`

These prompt further review; they are not trade instructions. Discord receives one concise alert per completed report, and duplicate sends are prevented.

### History, replay, and operations

Preserve signals, events, reports, failures, notification attempts, and provenance across restarts. Recorded market, news, duplicate-event, interrupted-data, and failure scenarios must replay through the same core flow used by live operation. Structured logs and basic status information must make health visible.

## Constraints and limitations

- Monitoring stops when the local application or computer is offline.
- Provider access may limit market coverage, watchlist size, and news timeliness.
- Volume supports significance but does not prove it; correlation does not prove causation.
- Classification, research, filings, and web sources can be incomplete, conflicting, or wrong.
- The application lacks the portfolio, tax, valuation, liquidity, and thesis context required for personalized financial advice.
- A single-process local application is an intentional MVP limit.

## Non-goals

- Trading, brokerage integration, autonomous decisions, rebalancing, or tax-aware advice.
- Comprehensive valuation, financial planning, or predictive price models.
- Complex technical strategies, full XBRL normalization, or sophisticated semantic clustering.
- Multi-provider failover, web or mobile interfaces, distributed infrastructure, or a complex multi-agent framework.

## MVP success criteria

The MVP is successful when:

1. A small configured watchlist can be monitored during regular market hours.
2. Interrupted data is detected and missing minute bars can be recovered.
3. Deterministic rules produce understandable market signals.
4. News is filtered and classified without researching every article.
5. Related signals become one deduplicated event.
6. A significant event produces one bounded research run.
7. The report follows its schema, cites evidence, and states uncertainty.
8. Discord receives one useful alert without duplicate delivery.
9. Event, report, failure, and notification history survive restarts.
10. Recorded scenarios replay through the live core flow.
11. External services can change without rewriting core logic.
12. API use stays within configured rate and cost limits.
