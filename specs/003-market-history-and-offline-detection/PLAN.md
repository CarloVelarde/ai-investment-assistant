# Implementation Plan: Market History and Offline Detection

**Document status:** Approved

## Approach

Add persisted market bars and detector baseline state, then implement pure fast and daily evaluators that emit Milestone 2 `MarketSignal` values. Wire offline fixture replay so bars flow through the same storage and detection boundaries live data will use later. Update event grouping to respect open vs closed market episodes. Keep fake research and console notification unchanged.

Work in thin vertical slices: models and bar storage first, then pure metrics/thresholds, then each detector mode with rearm state, then episode close, then scenario fixtures and pipeline wiring.

## Key decisions

### Schema

Extend SQLite (still built-in `sqlite3` only). Bump `DATABASE_VERSION` and keep `initialize()` safe to re-run.

New or extended groups:

| Data | Purpose |
| --- | --- |
| Market bars | Completed OHLCV history for watchlist symbols and `SPY` |
| Detector state | Last emitted importance (or clear) per `(ticker, rule, window, direction)` |
| Events | Add `episode_open` (default true for new events) and optional `closed_at` |

Use a stable bar id: `bar:{ticker}:{timeframe}:{start_at_isoformat}`.

### Models

Introduce a frozen `MarketBar` (and small enums for timeframe) in `models.py`. Keep detectors dependent on bars + history views, not on fixture JSON.

Extend `MarketSignal` measurement fields only if required for upside and non-decline rules (for example signed change ratio or separate magnitude + direction). Prefer backward-compatible fields: keep `price_decline_ratio` as a non-negative magnitude of adverse move for the signal’s direction, or rename carefully with tests if clearer (`price_move_ratio`). Document the chosen field meaning in code.

Milestone 1’s `MarketRecord` single-snapshot helper may remain for old tests until migrated; console and M3 scenarios should not depend on it for detection.

### Thresholds and pure metrics

Put threshold tables and metric functions in a small dedicated module (for example `market_metrics.py` or `detection/thresholds.py`) with no I/O:

- one-hour move, N-day return, drawdown from high, relative to `SPY`;
- importance from magnitude;
- volume dampening for the fast rule;
- rearm line = half of `MODERATE` magnitude.

Detectors call these pure functions, then decide emit vs quiet using detector state loaded from storage.

### Detector state machine

For each key `(ticker, rule, window, direction)`:

1. Load state (missing = armed/clear).
2. Compute metric and candidate importance.
3. If below `MODERATE` and past rearm line → mark clear/armed, no signal.
4. If at/above `MODERATE` and (armed or higher importance than last emit) → emit, save new last importance.
5. Else → quiet continuation.

Persist state in the same transaction style as other storage writes when a scenario step commits detection results.

### Episode open/close

Update storage finders used by the event manager:

- `find_direction_events` (and related helpers) return only **open** episodes for market grouping.
- After daily evaluation for a ticker, a small episode-maintenance step checks detector keys for each open direction on that ticker; if all keys for that direction are clear/armed, close the open episode.

Do not invent a second event manager. Close is a storage update + domain rule invoked from the offline market pipeline after detection, not from inside pure metric functions.

### Offline fixtures and pipeline

Add packaged fixtures under something like `fixtures/market_history/` with explicit bar JSON per scenario:

- abrupt drop (minute bars);
- gradual decline (daily bars);
- continuation (same severity, no second research);
- escalation (worsening severity or new window);
- recovery (stress then rearm/close);
- broad market (ticker + `SPY` daily bars for relative rule).

Pipeline flow:

```text
load bars → persist idempotently → evaluate fast/daily in time order
  → handle_signal for each emission → maintain episode close
  → process_pending (fake research + console notify)
```

Use `FixedClock` or a controllable clock stepped to bar times in tests.

### What not to build

- No Alpaca client, websockets, or network calls.
- No threshold config UI or env sprawl; constants in code are enough.
- No weekly job, Celery, Redis, or ORM.
- No Discord or real research changes.

## Data flow

```text
Fixture bars (JSON)
    → normalize to MarketBar
    → SQLite market_bars (idempotent)
    → Fast detector (on each completed 1Min)
    → Daily detector (on each completed 1Day)
    → optional episode close check
    → EventManager.handle_signal
    → EventManager.process_pending
    → fake research + console notification
```

Live Milestone 4 will replace only the left side (bar source), not the detectors’ core rules.

## Validation

- Unit tests for pure metrics and importance tables (including boundary inclusivity and rearm half-threshold).
- Unit tests for detector state: first cross, continuation quiet, escalation emit, rearm, replay after reopen.
- Storage tests for bar idempotency and episode_open filtering.
- Event-manager grouping tests: closed episode not reused; new event after close.
- Scenario tests for the six offline stories in the specification.
- Full repository checks: ruff format/check, mypy, pytest.

## Tradeoffs

- Fixed thresholds may need later tuning; Milestone 8 owns evidence-based retuning, not guesswork mid-feature.
- Closing only after **daily** “all keys clear” may leave an episode open across a multi-day chop until the daily scan sees clear state; that is intentional and simpler than continuous episode logic.
- Building daily bars from minutes is optional; shipping daily bars in fixtures is preferred for clarity.
- Symmetric UP rules add a little code but avoid painting the design into downside-only corners.

## Intentional limits

- Offline only; no stream health.
- News path unchanged except closed episodes are not market-join targets for new market signals.
- Notification cooldown still Milestone 7.
- Strong live-delivery claim/recovery still Milestone 7.
