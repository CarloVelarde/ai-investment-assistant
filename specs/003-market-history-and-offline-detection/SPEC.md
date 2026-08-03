# Feature Specification: Market History and Offline Detection

**Document status:** Approved

## Purpose

Remember offline market price history, detect abrupt and gradual movement with clear rules, and feed one shared event manager so related market stress becomes one evolving episode without duplicate research.

## Scope

### In scope

- Define and persist normalized market bars for watchlist symbols and `SPY`.
- Load offline fixture bar series into the same models and storage path later live data will use.
- Evaluate **completed** bars only with two deterministic modes that share one signal contract:
  - **Fast detector:** abrupt movement over about one hour of completed bars.
  - **Daily detector:** fixed after-close scan for five-day move, twenty-day move, drawdown from a recent high, and performance relative to `SPY`.
- Assign ordered importance (`MODERATE`, `HIGH`, `CRITICAL`) from explicit thresholds.
- Persist detector baseline / rearm state so crossings and rearms are stable across restarts and replays.
- Close market episodes when directional stress has clearly recovered so later unrelated moves start a new event.
- Route every emitted market signal through the existing Milestone 2 event manager, fake research, and console notification path.
- Provide offline scenarios for abrupt drop, gradual decline, continuation, escalation, recovery, and broad-market (`SPY`) context.

### Out of scope

- Live market streams, reconnection, stale-stream health, or provider SDKs (Milestone 4).
- Live news, AI classification, richer news matching (Milestone 5).
- Real AI research, Discord, scheduling daemons, or multi-process workers.
- User-configurable thresholds, custom scan cadences, or a separate weekly process.
- Incomplete in-progress bars, after-hours-only special sessions, options, crypto, or non-US symbols.
- Upgrading every Milestone 1 fixture format beyond what this feature needs.

## Behavior

### Market history

A **market bar** is one completed OHLCV period for one ticker. Bars store at least:

- ticker in standard form (`TSLA`);
- timeframe (`1Min` or `1Day`);
- bar start and end times (UTC);
- open, high, low, close, volume;
- whether the bar is complete;
- provider, feed, and retrieval time for replay.

The application stores bars in SQLite through the existing storage boundary (extend schema; no ORM). Replaying the same fixture bars into the same database is idempotent by a stable bar identity (ticker + timeframe + bar start).

Fast evaluation uses completed `1Min` bars. Daily evaluation uses completed `1Day` bars. If daily bars are not supplied in a scenario, the application may build them only from completed regular-session minute bars in a deterministic way documented in the plan; scenarios should still ship the bars they need so tests stay obvious.

Missing `SPY` history skips the relative-to-`SPY` rule for that day and records a clear diagnostic; other rules still run. Bad or incomplete bars are ignored for evaluation and do not crash the process.

### Shared signal contract

Detectors only emit `MarketSignal` values. They never write research queue state themselves. Each signal includes:

- unique stable `signal_id` (same inputs always produce the same id);
- ticker, time, importance, direction, rule name, market window;
- measurement fields needed to explain the crossing (baseline, observed move, supporting volume ratio when used);
- source details (provider, feed, retrieval time).

Windows used by this milestone:

| Window | Used by |
| --- | --- |
| `ONE_HOUR` | Fast abrupt rule |
| `FIVE_DAYS` | Daily multi-day and `SPY`-relative five-day rules |
| `TWENTY_DAYS` | Daily multi-day, recent-high drawdown lookback, and `SPY`-relative twenty-day rules |

Rule names (stable strings):

| Rule | Meaning |
| --- | --- |
| `abrupt_move` | Fast one-hour completed-bar move |
| `multi_day_move` | Five- or twenty-trading-day close-to-close move |
| `drawdown_from_high` | Close vs highest high over the lookback |
| `relative_to_spy` | Ticker return minus `SPY` return over the same horizon |

Direction is `DOWN` or `UP`. Offline scenarios and acceptance focus on **downside** stress; upside uses the same magnitude thresholds with opposite sign so the design stays symmetric for later use.

### Importance thresholds

All percentage moves use close-to-close (or close vs reference high) ratios as decimals (for example `0.05` = 5%). Importance is the **highest** level whose threshold the observation meets. Values are inclusive at the listed magnitude.

#### Fast — `abrupt_move` / `ONE_HOUR`

Compare the latest completed minute bar close to the completed bar close from **60 minute bars earlier** on the same ticker (if fewer than 61 completed minute bars exist, do not emit).

| Importance | Absolute move |
| --- | --- |
| `MODERATE` | ≥ 3% |
| `HIGH` | ≥ 5% |
| `CRITICAL` | ≥ 8% |

Volume support: compute volume ratio = latest bar volume / average volume of the prior 20 completed minute bars (if fewer than 20 prior bars, do not require volume). If the average is available and ratio **&lt; 1.5**, lower importance by one step (`CRITICAL`→`HIGH`, `HIGH`→`MODERATE`, `MODERATE`→ no emit). This keeps weak-volume wiggles quieter without inventing a second threshold system.

#### Daily — `multi_day_move` / `FIVE_DAYS` and `TWENTY_DAYS`

Compare the latest completed daily close to the close **N trading days earlier** (N = 5 or 20). Require at least N+1 daily bars.

| Importance | 5-day absolute move | 20-day absolute move |
| --- | --- | --- |
| `MODERATE` | ≥ 5% | ≥ 10% |
| `HIGH` | ≥ 8% | ≥ 15% |
| `CRITICAL` | ≥ 12% | ≥ 20% |

#### Daily — `drawdown_from_high` / `TWENTY_DAYS`

Reference high = highest high of the last **20** completed daily bars including today. Drawdown = `(reference_high - close) / reference_high` for downside (and symmetric for upside vs reference low if evaluating `UP`).

| Importance | Absolute drawdown |
| --- | --- |
| `MODERATE` | ≥ 8% |
| `HIGH` | ≥ 12% |
| `CRITICAL` | ≥ 18% |

#### Daily — `relative_to_spy` / `FIVE_DAYS` and `TWENTY_DAYS`

Ticker return and `SPY` return over the same N trading days. Relative = ticker return − `SPY` return. For downside stress, more negative is worse.

| Importance | Absolute relative underperformance |
| --- | --- |
| `MODERATE` | ≥ 5 percentage points |
| `HIGH` | ≥ 8 percentage points |
| `CRITICAL` | ≥ 12 percentage points |

Example: ticker −6%, `SPY` −1% → relative −5% → `MODERATE` downside.

### Crossing, continuation, and rearm

Detectors keep persisted state per key:

`(ticker, rule, window, direction)`

For each key:

1. **Clear / armed:** the measured metric is strictly better than the `MODERATE` threshold (not in stress).
2. **Cross / emit:** when armed (or never seen) and the metric reaches at least `MODERATE`, emit one signal at the earned importance and store that importance as the last emitted level.
3. **Escalation emit:** while still in stress, if the earned importance **rank is higher** than the last emitted level for that key, emit again at the new importance.
4. **Quiet continuation:** while still in stress at the same or lower importance than last emitted, emit nothing (continuation is visible only through saved history when another path records it; detectors stay quiet).
5. **Rearm:** when the metric improves past the rearm line, clear last emitted level and become armed again.

**Rearm line:** half the `MODERATE` magnitude for that rule and window.  
Example: five-day `MODERATE` is 5%, rearm when absolute move improves to **&lt; 2.5%**.

Replaying the same bars must not re-fire a crossing that is already reflected in saved detector state.

### Fast vs daily evaluation schedule (offline)

Offline runs advance a controllable clock with the fixture timeline:

1. Ingest bars in time order (idempotent).
2. After each newly completed `1Min` bar for a watched ticker, run the fast detector for that ticker.
3. After each newly completed `1Day` bar for a watched ticker, run the full daily detector for that ticker (all daily rules). Evaluate `SPY` daily bars first when both arrive so relative rules can use the same session date.
4. Each emitted signal is submitted to the event manager immediately.
5. After the scenario’s signals are submitted, process pending research/notification once (Milestone 2 contract), unless a scenario explicitly tests intermediate recovery.

There is no separate weekly job and no user-facing cadence setting.

### Market episodes (open and close)

Milestone 2 grouped any same ticker+direction event, including finished ones. This milestone defines **episode openness**:

- An event that groups market stress has `episode_open = true` while the directional episode is active.
- Market signal grouping attaches only to an **open** event for the same ticker and direction.
- If none exists, a new event is created and left open.
- News may still join an open market episode using Milestone 2’s “one clear match” rule; news does not reopen a **closed** market episode by itself in this milestone (Milestone 5 may refine news–episode behavior).

**Close rule (deterministic):** after a daily evaluation for a ticker, if there is an open market episode for that ticker and direction, and **every** detector key for that ticker and direction is armed/clear (no unrearmed stress state), mark the episode closed. Closing:

- sets `episode_open = false` and records `closed_at`;
- does **not** create research or notification by itself;
- leaves signals, reports, and history intact;
- causes a later new downside crossing to create a **new** event rather than reopen the old one.

Recovery scenarios prove: stress fires → research may run → price recovers → detector keys rearm → episode closes → quiet continuation creates no duplicate research → a later new drop can start a fresh event.

### Integration with the event manager

Existing Milestone 2 rules remain:

- exact signal-id dedup;
- same or lower importance saved quietly;
- higher importance, new window, or significant news requeues research;
- latest complete update only;
- notify eligibility re-check before send.

This milestone adds:

- only **open** episodes receive new same-direction market signals;
- episode close as above;
- market signals produced by real offline detection rather than hand-built demo signals alone.

The Milestone 1 single-record price/volume toy rule may be removed or reduced to a test helper once the fast/daily detectors cover the console path; the console offline scenario for this milestone must use bar history and the new detectors.

### Failure and non-triggering behavior

- Bars that are incomplete, out of order for identity, or for unknown tickers do not emit signals.
- Below-threshold moves never emit.
- Same-severity continuation never emits after the first crossing (until rearm + new crossing).
- Missing `SPY` skips only relative rules.
- Storage and detector evaluation errors surface as safe diagnostics or test failures; they must not corrupt unrelated tickers’ state.

## Acceptance criteria

- [ ] AC-01: Normalized market bars for a watchlist ticker and `SPY` can be saved and reloaded; repeating the same bar identity does not create duplicates.
- [ ] AC-02: The fast detector emits on an abrupt one-hour threshold crossing with the correct importance, and stays quiet for below-threshold and same-severity continuation until rearm.
- [ ] AC-03: The daily detector emits for five-day move, twenty-day move, drawdown-from-high, and relative-to-`SPY` rules using the stated thresholds and windows.
- [ ] AC-04: Importance escalation on the same rule/window (for example `MODERATE` then `HIGH`) emits a second signal; same-importance continuation does not.
- [ ] AC-05: Detector baseline/rearm state survives database reopen and prevents duplicate fires on replay of the same bars.
- [ ] AC-06: Abrupt-drop and gradual-decline offline scenarios each create open events and research-eligible work through the event manager without requiring news.
- [ ] AC-07: Escalation (worse severity or new window) requeues a single latest update without duplicate research for outdated intermediate updates.
- [ ] AC-08: Recovery clears detector stress, closes the open market episode, and does not start new research solely because of recovery/close.
- [ ] AC-09: After close, a later new qualifying drop creates a **new** event rather than attaching to the closed episode.
- [ ] AC-10: Broad-market scenario shows relative-to-`SPY` behavior correctly (company-specific underperformance vs market-wide move) with understandable signals.
- [ ] AC-11: Offline end-to-end path uses controllable time, temporary SQLite, fake research, and console notification; no network, secrets, or live providers.
- [ ] AC-12: This milestone adds no live integrations, Discord, workers, ORM, weekly process, or user-configurable cadence.
- [ ] AC-13: Ruff formatting and linting, mypy, and pytest pass.

## Constraints

- Detectors emit signals only; the event manager alone promotes research.
- Keep thresholds and rules in one obvious place (constants or a small pure module), not scattered magic numbers.
- Prefer pure functions for metric calculation so tests do not need a database unless testing persistence.
- Preserve provenance on bars and signals.
- Keep one process and the existing SQLite write boundary; bump schema version carefully and keep setup safe to repeat.
- Prefer fixed defaults now; product-level user configuration of thresholds can come later without changing the signal contract.
- Never issue trade instructions or connect a brokerage.

## Open questions

- None. Thresholds, rearm, and episode close are fixed for this milestone; tune later only with replay evidence (Milestone 8).
