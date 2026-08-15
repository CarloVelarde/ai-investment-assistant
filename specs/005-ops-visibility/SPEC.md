# Feature Specification: Ops Visibility

**Document status:** Approved

## Purpose

Make a live run look alive when the tape is quiet, and give an optional human-readable story of what the app is doing. Default logs stay the quiet JSON `INFO` path people already have.

This is operator tooling. It does not change market rules, the event manager, or Milestone 5 news work.

## Scope

### In scope

- An on/off **heartbeat** on the regular application logger.
- A separate optional **watch log** that narrates a run end to end in plain text.
- Environment settings for both, off by default, independent of each other and of `LOG_LEVEL` / `LOG_JSON`.
- Heartbeat useful with `LOG_LEVEL=INFO` and `LOG_JSON=true` (including weekends and after hours).
- Tests with fakes and a controllable clock. No live Alpaca in pytest.

### Out of scope

- Changing default `LOG_LEVEL` or `LOG_JSON`.
- Making heartbeat depend on the watch log, or the reverse.
- A user-configurable heartbeat interval (fixed constant for now).
- Log files, rotation, a second process, or a metrics service.
- Raw websocket/REST packet dumps.
- Live news, Discord, real research, or detector/threshold changes.

## Behavior

### Settings

Prefix remains `INVESTMENT_ASSISTANT_`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | Unchanged. Standard logger level. |
| `LOG_JSON` | `true` | Unchanged. Standard logger format. |
| `HEARTBEAT` | `false` | When `true`, emit a short status line on a fixed interval. |
| `WATCH_LOG` | `false` | When `true`, enable the separate watch logger. |

Allowed combinations: neither, heartbeat only, watch log only, or both.

### Heartbeat

Heartbeat uses the **existing** application logger (not a new logger). It therefore follows `LOG_JSON` and `LOG_LEVEL`.

When heartbeat is on and the live loop is running:

- About every **60 seconds**, emit one short line that the process is still watching.
- Include enough to interpret idle time: regular session open or closed, that it is waiting on the stock socket, age of the last websocket message if any, and last `SPY` regular-session minute if any.
- Fire on weekends and after hours. A silent IEX tape is not a reason to skip the beat.
- Do not emit when heartbeat is off, including under default settings.
- Offline fixture mode is a one-shot run; no heartbeat loop is required there.

The interval is a fixed constant. Do not add another env knob unless live use shows a need.

Never put the API secret, key id, or auth payload in the heartbeat.

### Watch log

Watch log is a **separate** named logger (for example `investment_assistant.watch`). It is always human-readable text so it stays usable even when the standard logger is JSON.

When watch log is on, narrate the user-visible path:

- live vs offline, watchlist, feed, database path;
- backfill counts and live cutoff;
- quiet replay vs a bar that can emit;
- stock socket connected and subscribed (`bars` and `updatedBars` only);
- each completed minute: ticker, bar vs update, time, stored-only vs evaluated;
- after-close daily: session date, bar count, scan outcome;
- event manager: new case, quiet continuation, escalation, episode close;
- fake research and console notification;
- disconnect, stale, reconnect, and gap fill.

When watch log is off, none of those extra lines appear.

Never put secrets in the watch log. Do not subscribe extra stream channels to get more log text.

### Failures

- Invalid boolean env values fail settings load the same way other settings do.
- A logging failure must not crash detection or the live loop.
- Turning these on must not open a second stock socket or the news socket.

## Acceptance criteria

- [x] AC-01: Default settings emit no heartbeat and no watch-log lines. `LOG_LEVEL=INFO` and `LOG_JSON=true` stay the defaults.
- [x] AC-02: `HEARTBEAT=true` with default JSON `INFO` emits a heartbeat on the regular logger about every 60 seconds during a live loop, including when the session is closed and no minutes arrive.
- [x] AC-03: `HEARTBEAT=false` never emits a heartbeat, even if watch log is on.
- [x] AC-04: `WATCH_LOG=true` emits a plain-text narrative of a canned live cycle (backfill → subscribe → minute → daily or idle). `WATCH_LOG=false` does not.
- [x] AC-05: Heartbeat and watch log can be enabled independently. Secrets never appear. Tests use fakes and no network. Ruff, mypy, and pytest pass.

## Constraints

- One process. No new database, worker, or dependency for logging.
- Detectors still only emit signals. The event manager still owns research.
- Heartbeat time is driven by the application clock so tests stay deterministic.
- Do not log credentials, `.env` contents, or auth frames.

## Open questions

- None. Interval stays 60 seconds until live use says otherwise.
