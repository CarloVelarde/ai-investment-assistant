# Feature Specification: Live Session Hardening

**Document status:** Approved

## Purpose

Stop unfinished daily bars from creating research during the regular session, and notice a large overnight or weekend gap when the next session opens. Do this before Milestone 5.

This is the next implementation slice. Milestone 4’s stock socket stays as shipped.

## Why now

A live trial on **Wednesday 19 Aug 2026** (regular session open, IEX, watchlist TSLA / AMD / SPY) showed:

1. **Bug — in-progress REST daily treated as complete.** Startup backfill accepted Alpaca’s running `1Day` bar (`end_at` stamped 16:00 ET). At 10:25 ET that emitted AMD `multi_day_move` / 20-day **HIGH** (−15.79%). Through Tuesday’s completed close the same rule was only −11% (MODERATE) and should have stayed quiet. A restart at 10:38 ET moved today’s running closes (TSLA 339.77 → 343.77) and emitted a new TSLA `relative_to_spy` / 5-day **MODERATE** that did not exist on the first start.
2. **Missing check — overnight / weekend gap.** There is no rule for “yesterday’s close vs this morning’s open.” The 1-hour detector can catch a gap only if the process already has yesterday’s last hour of minutes. A morning start backfills **today’s session only** and needs 61 minute bars to emit; by then the gap has rolled out of the window.

What already worked in that trial: live mode, REST backfill, one IEX socket, every-minute AMD/TSLA/SPY bars, the fast detector (AMD −3.18% in 60 minutes, then quiet continuation), heartbeat, AMD restart dedup, secrets kept out of logs, Ctrl+C → `Stopped`.

Trial notes on the throwaway branch `scratch/live-run-check-2026-08-19` are evidence, not product truth.

## Scope

### In scope

- Treat a REST `1Day` bar as incomplete while that regular session is still open.
- Emit daily signals only from **completed** daily bars (`end_at <= now`, or the clock says that session has closed).
- Do not write daily `last_emitted_importance` from an incomplete daily bar.
- After the regular close, today’s completed `1Day` bar may still run the existing daily detector.
- Add market rule `session_gap` / window `SESSION_OPEN`: prior completed regular close vs today’s regular open.
- Evaluate that gap once per symbol per regular session through the existing event manager, fake research, and console notify.
- Tests with fakes and a controllable clock. No live Alpaca in pytest.
- Regression coverage that matches the live-trial daily-bar failure.

### Out of scope

- Changing Milestone 3 fast or daily **thresholds** (3/5/8 hour, 5/8/12 five-day, and the rest stay).
- Same-day open-to-close as its own rule.
- A one-day close-to-close rule (prior close vs today’s close).
- Watch-log per-bar backfill narration (spec 005 leftover; optional later).
- Live news, the news socket, real research, Discord, workers, or a second process.
- Tuning stale timers from one trial (Milestone 8).
- Reopening Milestone 4 socket tasks.

## Behavior

### Bug: in-progress REST daily

Alpaca `GET /v2/stocks/bars` with `timeframe=1Day` and `end=now` can return **today** while the cash session is still open. That row is a running daily, not a finished day.

Required behavior:

1. If a `1Day` bar’s regular-session `end_at` is still in the future, it is **not complete**. Persist it only if we must, with `is_complete=false`, or omit it from evaluation. Incomplete bars do not run daily detectors.
2. Replay may emit a daily bar only when `live_cutoff <= bar.end_at <= now`.
3. Quiet replay of **completed** older days still updates detector state without research (unchanged).
4. A restart during the regular session must not create or escalate a daily event just because today’s running close moved.
5. After the clock says the regular session is closed, fetch and evaluate the **completed** `1Day` bars as Milestone 4 already specifies.

The live-trial AMD HIGH and TSLA relative-to-SPY alerts at 10:25–10:38 ET must not happen under these rules.

### Feature: session-open gap

**Question answered:** “Did this name jump a lot between the last regular close and this regular open?”

| Piece | Value |
| --- | --- |
| Rule | `session_gap` |
| Window | `SESSION_OPEN` |
| Baseline | Close of the last **completed** `1Day` bar (prior regular session, including Friday → Monday) |
| Observed | Today’s regular open: the `open` of the first regular-session `1Min` bar (09:30 ET start). If that minute is missing on IEX, use the first available regular-session minute’s `open` and record a diagnostic |
| Move | `(observed - baseline) / baseline` |
| Direction | `UP` if observed > baseline, `DOWN` if observed < baseline |

Do **not** use today’s REST `1Day` open or close for this check. That is the same running bar.

#### Thresholds

Same inclusive ladder as the fast rule:

| Importance | Absolute gap |
| --- | --- |
| `MODERATE` | ≥ 3% |
| `HIGH` | ≥ 5% |
| `CRITICAL` | ≥ 8% |

Rearm line: strictly below half of `MODERATE` (1.5%). Crossing, escalation, quiet continuation, and rearm follow the Milestone 3 state machine on key `(ticker, session_gap, SESSION_OPEN, direction)`.

No volume dampening on this rule. A gap is a print-to-print jump, not a one-hour grind.

#### When it runs

Once per symbol per regular session, when **both** inputs exist:

- After startup backfill, if today’s first regular-session minute is already stored.
- Otherwise, when that first regular-session minute is ingested.

If the app starts after the close the same day, still evaluate today’s gap once if detector state does not already have that crossing. If the app starts on a weekend or later day, do **not** late-fire an older session’s gap; the after-close daily scan already had its turn.

A restart the same session must not research the same gap again.

#### What it does not do

- It does not replace the 1-hour detector.
- It does not replace the after-close daily scan.
- It does not measure today’s open vs today’s close.

### Event manager

`session_gap` is an ordinary market signal. Same direction attaches to an open episode; a new horizon may requeue. Opposite direction does not attach. Fake research and console notify stay as they are.

### Failures

- Missing prior daily close: no emit, diagnostic, other symbols continue.
- Missing today’s open: wait until a regular-session minute arrives; do not invent an open.
- Secrets never appear in logs or diagnostics.

## Acceptance criteria

- [ ] AC-01: During a regular session, a fake REST `1Day` bar for **today** that would cross a daily threshold does **not** emit, does **not** create an event, and does **not** write daily `last_emitted_importance`. Completed older days still quiet-replay.
- [ ] AC-02: After the clock is closed, that same day’s **completed** `1Day` bar may emit through the existing daily detector.
- [ ] AC-03: A second backfill in the same open session with a *different* running daily close does not create a new daily event or escalate solely because the running close moved. (Regression: TSLA 339.77 → 343.77 vs SPY.)
- [ ] AC-04: Prior close 450, today’s 09:30 open 480 (+6.67%) emits `session_gap` `UP` at `HIGH` once. A restart the same session does not research it again.
- [ ] AC-05: A +2% gap does not emit. A −3% gap emits `DOWN` `MODERATE`. Friday close → Monday open uses the same rule.
- [ ] AC-06: Starting after 09:30 the same day still evaluates the gap from the stored first regular-session minute and the prior completed daily close. Starting with only today’s minutes (no 61-bar hour history) still can emit `session_gap`.
- [ ] AC-07: Milestone 3 fast/daily thresholds are unchanged. No news client, Discord, workers, or second socket. Tests use fakes and no network. Ruff, mypy, and pytest pass.

## Constraints

- Detectors emit signals only. The event manager still owns research.
- One process. Built-in `sqlite3` only.
- Convert provider objects to `MarketBar` before this logic.
- Time and I/O stay injectable.
- Do not change D-023: this is review, not day trading.

## Open questions

- None. Thresholds match the fast ladder until live use says otherwise (Milestone 8).
