# AI Investment Assistant — Product Specification

**Status:** Approved high level MVP  
**Last reviewed:** July 31, 2026

## Document Purpose

This document defines what the AI Investment Assistant is, who it serves, what the MVP must do, and what is intentionally outside its scope.

Implementation structure and system boundaries belong in [`ARCHITECTURE.md`](ARCHITECTURE.md). Development sequencing belongs in [`ROADMAP.md`](ROADMAP.md). Feature-specific behavior and acceptance criteria belong under `specs/`.

## Product Summary

AI Investment Assistant is a personal market-monitoring and research tool for a long-term investor. It watches a small list of US stocks, detects potentially meaningful market or news events, investigates selected events, and sends one focused report through Discord.

It is intended to help answer:

- What happened to this stock?
- What most likely caused the movement?
- Is the event company-specific, industry-wide, or market-wide?
- Does the evidence suggest temporary noise or a potentially fundamental development?
- What evidence supports that assessment?
- What remains uncertain or deserves further review?

The application does not trade, predict the market with certainty, or make final investment decisions. The user remains responsible for every decision.

## Target User

The MVP is built for one primary user: its owner, a long-term investor interested in understanding significant events and identifying potential buying opportunities.

It is not designed for day traders, financial advisers, institutional teams, or general public use.

## Project Intent

This is a solo side project. It should be:

- Useful in the owner's real investing workflow.
- Small enough for one developer to build, operate, test, and understand.
- Extensible at meaningful boundaries without imitating an enterprise platform.
- Technically substantial and easy to explain in interviews.
- A practical demonstration of deterministic detection, event-driven processing, selective AI use, persistence, reliability, and testing.

Complexity is justified only when it supports the core product loop, protects a real boundary, reduces meaningful risk, or creates a clear engineering lesson.

## Core Product Loop

> Detect a meaningful event → correlate related signals → assemble trustworthy evidence → produce one focused report → notify once.

This is the central proof of the MVP. Features that do not directly support this loop should generally be deferred.

## Product Principles

### Deterministic logic before AI

Ordinary code handles facts and rules that can be evaluated directly, including price movement, volume anomalies, watchlist membership, recency, cooldowns, and duplicate detection.

AI is reserved for tasks that benefit from semantic judgment, especially news classification and focused event research.

### Cheap detection, selective research

Continuous monitoring should remain inexpensive. A capable research model runs only after an event has passed a significance threshold.

### Classification and research are separate jobs

News classification decides whether an article is relevant and significant enough to escalate. Research investigates what happened, evaluates the evidence, and identifies uncertainty.

The classifier must remain narrow, structured, and inexpensive. It is not a smaller version of the research agent.

### Evidence over authority

Reports must distinguish evidence from inference, acknowledge competing explanations, cite important sources, and communicate uncertainty. They should support review, not issue authoritative buy or sell commands.

### Reliability before sophistication

Recovering from interrupted data streams, preventing duplicate alerts, preserving event history, replaying recorded scenarios, and enforcing cost limits matter more than adding many indicators or a complicated agent framework.

## MVP Scope

### 1. Watchlist and configuration

The user can configure:

- A small set of stocks to monitor.
- Whether each stock is owned or only watched.
- Optional cost-basis information or personal notes.
- Detection thresholds.
- Event-correlation windows and notification cooldowns.
- Regular-market-hours behavior.
- Notification preferences.
- API usage and cost limits.

The MVP does not connect to a brokerage account.

### 2. Market monitoring

The application monitors minute-level market activity for the configured watchlist and a small number of comparison symbols.

It uses recent and historical context to detect understandable signals such as:

- Significant price movement over configured windows.
- Movement relative to the broad market, initially represented by `SPY`.
- Unusual same-feed volume.
- Simple recent volatility context when useful.

The application must detect stale or interrupted live data and recover missing minute bars when possible.

### 3. News monitoring and triage

The application receives company-related news and filters it before using AI.

Filtering considers:

- Watchlist relevance.
- Article recency.
- Source acceptability.
- Important event categories.
- Duplicate or near-duplicate stories.
- Classifier-call limits.

Articles that pass these rules receive a small, structured AI classification covering relevance, category, likely significance, direction, confidence, and a short rationale.

### 4. Event correlation

Related market and news signals are grouped into one event rather than producing separate research jobs and repeated notifications.

The MVP must handle:

- Market movement with related news.
- Market movement without an obvious news explanation.
- Significant news before a price reaction.
- Broad-market or sector-wide movement.
- Multiple articles about the same underlying event.
- Materially new evidence for an event that was already reported.

Events retain enough lifecycle state to support deduplication, cooldowns, retries, and restart-safe behavior.

### 5. Focused event research

Research runs only when an event is significant enough to justify the cost.

The research process should answer:

1. What happened?
2. What evidence most likely explains it?
3. Is it company-specific, industry-wide, or market-wide?
4. Is there evidence of a potentially fundamental development?
5. What credible competing explanations exist?
6. What remains unknown?
7. What research posture is justified by the available evidence?

Research is bounded by time, tool-call, source-size, rate, and cost limits.

### 6. Structured report and notification

Each completed research run produces a validated report containing:

- The ticker, company, and trigger time.
- The triggering market or news signals.
- A concise event summary.
- The likely cause and credible competing explanations.
- Relevant price, volume, market, and sector context.
- Supporting evidence with source information.
- Potentially bullish and bearish considerations.
- An assessment of temporary noise versus a potentially fundamental event.
- Confidence, uncertainties, and missing information.
- A cautious research posture.

Allowed research postures are:

- `MONITOR`
- `INVESTIGATE_FURTHER`
- `POTENTIAL_OPPORTUNITY_TO_REVIEW`
- `WAIT_FOR_CLARITY`

These are prompts for further review, not trade instructions.

Discord receives one concise alert summarizing the event, likely explanation, confidence, posture, key uncertainty, and most important sources. Duplicate sends must be prevented.

### 7. History, replay, and operational visibility

The application preserves the information needed to understand prior signals, events, reports, failures, and notification attempts across process restarts.

Recorded market and news scenarios can replay through the same core product flow used for live events. Replay must cover representative cases such as:

- A large price drop without news.
- Major news without a large price movement.
- Duplicate and updated news stories.
- A broad-market selloff.
- Interrupted data and missing bars.
- Repeated triggers from the same event.
- Research or notification failure.

The user must be able to observe whether monitoring is healthy through structured logs and basic status information.

## Product Constraints and Limitations

- The MVP runs locally, so monitoring stops when the application or computer is offline.
- Free market data access may represent only part of US trading activity and may limit the number of simultaneously monitored symbols.
- News access and timeliness depend on provider availability and account entitlement.
- Volume is supporting evidence, not definitive proof of significance.
- Correlation rules cannot guarantee that several signals share the same cause.
- News classification and research conclusions can be wrong.
- Web sources may conflict, filings may be difficult to interpret, and some price movements may remain unexplained.
- The application does not have the complete personal, portfolio, tax, valuation, liquidity, or thesis information required for personalized financial advice.
- A single-process local application is an intentional MVP tradeoff, not a design for unlimited scale.

These limitations should be visible and handled honestly rather than hidden behind false confidence.

## Explicit Non-Goals

The MVP will not include:

- Automatic trading or order execution.
- Brokerage account integration.
- Autonomous buy or sell decisions.
- Portfolio rebalancing or optimization.
- Portfolio aware or tax aware recommendations.
- Comprehensive equity valuation models.
- Continuous financial planning.
- Predictive price models.
- Complicated technical-analysis strategies.
- Perfect SEC filing interpretation or full XBRL normalization.
- Sophisticated semantic news clustering.
- Multi-provider failover.
- A web or mobile interface.
- Distributed workers, microservices, or cloud-scale infrastructure.
- A complex multi-agent framework.

These may be reconsidered only after the core product loop works reliably and a real need is demonstrated.

## MVP Success Criteria

The MVP is successful when it can reliably demonstrate that:

1. A small configured watchlist can be monitored during regular market hours.
2. Interrupted live data is detected and missing minute bars can be recovered.
3. Simple deterministic rules produce understandable market signals.
4. News is filtered and classified without researching every article.
5. Related market and news signals become one deduplicated event.
6. A significant event produces a bounded, evidence based research run.
7. The report follows the required structure and cites its evidence.
8. Discord receives one useful alert instead of repeated notifications.
9. Event, report, failure, and notification history survive process restarts.
10. Recorded scenarios replay through the same core flow used by live events.
11. External services can be changed without rewriting the product's core logic.
12. API use remains within configured rate and cost limits.

## First Development Milestone

The offline walking skeleton is the first implementation milestone, not the complete MVP.

It proves this end-to-end flow without internet access or secrets:

> Fixture data → normalization → deterministic detection → signal correlation → event creation → fake research report → console notification.

It uses fake external services, one market rule, one news filter, one triggering scenario, and one non-triggering scenario. Later milestones replace those fakes and expand the behavior until the MVP success criteria above are satisfied.

The detailed scope and acceptance criteria for this milestone belong in `specs/001-offline-walking-skeleton/`.

## Final Product Definition

The AI Investment Assistant MVP is a local application that monitors a small stock watchlist, uses deterministic rules and inexpensive news classification to identify significant events, correlates related signals, launches bounded evidence-based research only when justified, and sends one structured Discord report while leaving all investment decisions and trading actions to the user.
