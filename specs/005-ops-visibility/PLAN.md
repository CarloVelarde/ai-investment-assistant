# Implementation Plan: Ops Visibility

**Document status:** Approved

## Approach

Add two independent opt-ins. Do not change default logging or any market/event code path except to emit optional status lines.

1. Settings: `heartbeat: bool = False` and `watch_log: bool = False` on the existing Pydantic settings.
2. Heartbeat: in the live loop, if `heartbeat` is on and the clock says the interval elapsed, log one INFO line on the regular `investment_assistant.main` (or live-loop) logger. Reuse `StreamHealth` last-seen times. Skip the extra knob for interval; use a module constant of 60 seconds.
3. Watch log: a small helper around logger `investment_assistant.watch`. When `watch_log` is on, configure that logger with a text formatter that prints useful extras. Call it at existing stage boundaries (backfill, handshake, ingest, daily, event/notify, reconnect). When off, the helper is a no-op.
4. Keep JSON formatter behavior for the standard logger. Optionally include heartbeat fields as `extra=` so JSON lines carry session/open/last-message data. Do not require a JSON-formatter rewrite for this slice beyond what heartbeat needs to be readable.

## Key decisions

- Heartbeat is **not** its own logger and is **not** gated by watch log.
- Watch log is always plain text.
- Heartbeat interval is fixed at 60 seconds.
- Offline console path does not heartbeat.
- No new packages.

## Data flow

```text
env HEARTBEAT / WATCH_LOG (both default false)
    → Settings
    → live loop
        → every cycle: maybe watch-log stage lines
        → every 60s if HEARTBEAT: one regular INFO/JSON status line
    → default path unchanged when both false
```

## Validation

- Settings tests: defaults off; each flag loads from env; they do not affect `live_mode`.
- Live-loop test with fake provider + stepping clock: heartbeat on → one line at 60s, not at 59s; session closed still beats.
- Heartbeat off + watch log on → no heartbeat line.
- Watch log on → canned cycle produces expected stage text; secret absent.
- Watch log off → those lines absent.
- Existing main/live tests still pass with defaults.
- Full repo checks.

## Tradeoffs

- Mixed stdout (JSON standard lines + text watch lines) when both JSON and watch log are on. Accepted so watch log stays readable.
- Heartbeat every 60s can look chatty if left on for days. It is opt-in.

## Intentional limits

- No log file.
- No packet dump.
- No news socket.
- No interval env var yet.
