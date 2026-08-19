# Feature Specification: Live Session Hardening

**Document status:** Approved

## Purpose

Fix two live-market problems found on 19 Aug 2026, then stop. Do not start Milestone 5 until this spec is done. Milestone 4’s stock socket stays as shipped.

## Issues

### Issue 1 — Unfinished daily prices fire after-close rules

**What is wrong**

The after-close rules are `multi_day_move`, `drawdown_from_high`, and `relative_to_spy`. They are supposed to run **after 16:00 ET** on a **finished** day’s close.

Alpaca’s history API still returns **today’s row** while the market is open. That row is the price so far, not the close. The app marked it complete, stamped its end time as 16:00 ET, and ran the after-close rules at 10:25 ET.

What the trial did:

- AMD: 20-day `multi_day_move` fired **HIGH** (−15.79%) at 10:25 ET. Using Tuesday’s real close, the same rule was only −11% (MODERATE) and should have stayed quiet.
- Restart at 10:38 ET: today’s still-moving closes had changed (TSLA 339.77 → 343.77). TSLA vs `SPY` over five days then crossed 5% and fired `relative_to_spy` **MODERATE**. A restart changed the daily story because the “close” was still moving.

**What we do instead**

- Treat today’s daily row as unfinished until the regular session has closed.
- Do not run `multi_day_move`, `drawdown_from_high`, or `relative_to_spy` on that unfinished row.
- Do not save “we already emitted” daily state from that unfinished row.
- After 16:00 ET, run those three rules on the **finished** day, same as Milestone 3 and 4 already specify.
- A restart while the market is open must not create or escalate a daily event just because today’s price moved.

### Issue 2 — Overnight and weekend gaps are not checked

**What is wrong**

There is no rule for “the last regular close vs this morning’s open.”

Example: AMD closes Wednesday at $450 and opens Thursday at $480. That +6.7% overnight jump is a reason to look. The current rules do not ask that question.

- The 1-hour rule (`abrupt_move`) compares the last 60 **minute** closes. It can include the jump only if the process already stored yesterday’s last hour. Start the app Thursday morning and it backfills **today only**. It needs 61 minute bars before it will emit; by 10:30 ET the window is all Thursday and the overnight jump is gone.
- The after-close rules compare closes over 5 or 20 days. They do not measure last close → this open, and they must not run until the day is finished (Issue 1).

**What we do instead**

Add one rule, `session_gap`: last finished regular close vs today’s regular open (the 09:30 ET open). Run it **once per symbol per session** when both numbers exist. Use the same 3% / 5% / 8% steps as the 1-hour rule. A same-session restart does not research the same gap again.

This is not today’s open vs today’s close. It is not a 1-hour move. It does not replace the after-close scan.

### What the trial already got right

Live mode, history backfill, one IEX socket, minute bars for AMD / TSLA / SPY, the 1-hour detector (AMD −3.18% then quiet), heartbeat, AMD not re-notified on restart, no secrets in logs, Ctrl+C logged `Stopped`.

Scratch notes on `scratch/live-run-check-2026-08-19` are evidence only. This spec is the product rule.

## Scope

### In scope

- Issue 1: do not run `multi_day_move`, `drawdown_from_high`, or `relative_to_spy` on today’s unfinished daily price. After the close, those rules still run on the finished day.
- Issue 2: add `session_gap` (last finished regular close vs today’s regular open). Evaluate once per symbol per session. Detectors emit; the event manager still decides research.
- Tests with fakes and a controllable clock. No live Alpaca in pytest.
- Regression coverage for the trial’s AMD 20-day HIGH and TSLA vs `SPY` restart.

### Out of scope

- Changing Milestone 3 fast or daily **thresholds** (3/5/8 hour, 5/8/12 five-day, and the rest stay).
- Same-day open-to-close as its own rule.
- A one-day close-to-close rule (prior close vs today’s close).
- Watch-log per-bar backfill narration (spec 005 leftover; optional later).
- Live news, the news socket, real research, Discord, workers, or a second process.
- Tuning stale timers from one trial (Milestone 8).
- Reopening Milestone 4 socket tasks.

## Behavior

### Unfinished daily prices (Issue 1)

Alpaca history can return **today’s** daily row while the cash session is still open. That row is not a finished day.

Required behavior:

1. If a daily bar’s regular-session end is still in the future, it is **not complete**. Do not run `multi_day_move`, `drawdown_from_high`, or `relative_to_spy` on it.
2. Replay may send a daily bar to those rules only when the bar has already ended and that end is at or after today’s live cutoff (`live_cutoff <= bar.end_at <= now`).
3. Quiet replay of **finished** older days still updates detector state without research (unchanged).
4. A restart during the regular session must not create or escalate a daily event just because today’s still-moving price changed.
5. After the clock says the regular session is closed, fetch and evaluate the **finished** daily bars as Milestone 4 already specifies.

The trial’s AMD 20-day HIGH at 10:25 ET and TSLA vs `SPY` alert on restart must not happen under these rules.

### Session-open gap (Issue 2)

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

- [x] AC-01: During a regular session, a fake REST `1Day` bar for **today** that would cross a daily threshold does **not** emit, does **not** create an event, and does **not** write daily `last_emitted_importance`. Completed older days still quiet-replay.
- [x] AC-02: After the clock is closed, that same day’s **completed** `1Day` bar may emit through the existing daily detector.
- [x] AC-03: A second backfill in the same open session with a *different* running daily close does not create a new daily event or escalate solely because the running close moved. (Regression: TSLA 339.77 → 343.77 vs SPY.)
- [x] AC-04: Prior close 450, today’s 09:30 open 480 (+6.67%) emits `session_gap` `UP` at `HIGH` once. A restart the same session does not research it again.
- [x] AC-05: A +2% gap does not emit. A −3% gap emits `DOWN` `MODERATE`. Friday close → Monday open uses the same rule.
- [x] AC-06: Starting after 09:30 the same day still evaluates the gap from the stored first regular-session minute and the prior completed daily close. Starting with only today’s minutes (no 61-bar hour history) still can emit `session_gap`.
- [x] AC-07: Milestone 3 fast/daily thresholds are unchanged. No news client, Discord, workers, or second socket. Tests use fakes and no network. Ruff, mypy, and pytest pass.

## Constraints

- Detectors emit signals only. The event manager still owns research.
- One process. Built-in `sqlite3` only.
- Convert provider objects to `MarketBar` before this logic.
- Time and I/O stay injectable.
- Do not change D-023: this is review, not day trading.

## Open questions

- None. Thresholds match the fast ladder until live use says otherwise (Milestone 8).
