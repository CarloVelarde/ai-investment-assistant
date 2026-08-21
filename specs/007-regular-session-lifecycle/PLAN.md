# Implementation Plan: Regular Session Lifecycle Correction

**Document status:** Approved

## Approach

Make four small corrections inside the existing market-data and live-loop
boundaries. Add no provider, process, scheduler, or configurable policy.

1. Use the existing regular-session predicate as the single gate for accepting
   live `1Min` bars. Ignore rather than retain new extended-hours minutes, and
   make the fast detector select regular minutes even when an older database
   already contains extended rows.
2. Reorder the live loop around provider session state: startup REST recovery and
   pending work first; socket work only while open; daily and idle work while
   closed. Reuse the existing open, close, reconnect, and heartbeat functions.
3. After an open-session socket subscription, call the existing minute gap-fill
   path once to cover the startup handoff.
4. During startup replay, identify the latest completed daily bar per symbol as
   eligible to emit. Older completed daily bars remain quiet warmup; current
   unfinished daily bars remain ineligible.

## Existing implementation seams

- `market_data.is_regular_session_minute` already owns the 09:30–16:00 ET
  predicate; do not create a second time rule.
- `live_ingest.ingest_stream_minute`, `_replay_bars`, and `fill_minute_gap` are
  the stream, REST replay, and handoff/reconnect paths that must share the gate.
- `market_detection.detect_fast_from_storage` selects the 61-bar history. Its
  regular-minute selection must remain bounded and work with legacy extended
  rows.
- `main.run_live_session` currently opens and consumes the stream before it
  checks session state. `AlpacaMarketData.iter_stream_minutes` can also open the
  stream itself, so the closed branch must not call it.
- `holds_stock_stream` means a transport is configured; it is not connection
  state. Track or expose connection state explicitly rather than changing that
  meaning.
- Existing fake market data, fake stock transport, fixed/stepping clocks, and
  recording research/notifier patterns should be extended for tests.

## Key decisions

- Regular `1Min` starts are exactly `09:30 <= start < 16:00` ET.
- New non-regular minute bars are ignored and not persisted. Existing rows are
  left in place but filtered from fast history; no destructive migration.
- Full-day regular REST replay remains the simple startup recovery strategy.
- The socket follows provider `session.is_open`; it is not a recovery dependency.
- Closed starts do not exit. The same loop waits and can connect at a later open.
- Closed waiting uses the existing injected sleeper and heartbeat path; it must
  not spin just because a socket transport is configured.
- Pending events are processed after durable REST acceptance without waiting for
  socket success.
- One gap fill follows the initial successful subscription for the startup
  history/stream handoff. The existing reconnect path already performs its own
  gap fill and must not receive a redundant second one.
- Only the latest completed daily session receives catch-up eligibility. Older
  daily bars, prior fast moves, and old session gaps remain quiet.
- Determine each ticker’s latest completed daily catch-up candidate before replay
  so older warmup bars cannot be mistaken for the candidate. The candidate still
  uses normal detector state and signal IDs.
- Stable bar IDs, signal IDs, detector `last_evaluated_at`, and event-manager
  rules continue to provide idempotency.

## Data flow

```text
startup
  → fetch daily lookback + relevant regular-session minutes
  → ignore non-regular 1Min bars
  → quiet-replay old daily/minute history
  → allow current-session recovery + latest completed daily catch-up
  → evaluate current session_gap only
  → process pending events

main loop
  → read provider session
  → open:
      ensure one stock socket
      one post-subscription REST gap fill
      consume regular-session frames
      stale/reconnect recovery
  → closed:
      close any stock socket
      run latest completed daily once
      do not reconnect
  → process pending events
  → heartbeat / wait
```

## Validation

- Unit-test the exact 15:59 accepted / 16:00 rejected boundary for REST and
  stream input.
- Seed an older SQLite database with premarket/after-hours rows and prove the
  next 09:30 bar has no 61-bar regular history and emits no fast signal.
- Reproduce the 10:00–11:05 ET stop and 19:00 same-day restart with one database.
  Assert full-day REST recovery, correct signals, gap deduplication, pending
  processing, and zero socket calls.
- Drive fake session transitions closed → open → closed. Assert one connection,
  one subscription, clean close, no closed reconnect, and continued heartbeat or
  loop progress through the injected sleeper without a busy loop.
- Insert a fake bar between startup backfill and subscription; return it from the
  handoff gap fill and again as a buffered frame. Assert one bar, signal, event,
  research run, and notification.
- Reproduce a missed Monday daily close with first restart Tuesday morning and a
  missed Friday close with first restart on the weekend. Assert the latest daily
  crossing emits once and later restarts stay quiet. Assert old fast and gap
  signals do not emit.
- Preserve existing unfinished-daily, session-gap, reconnect, stream, heartbeat,
  event-manager, and offline behavior tests.
- Run:

  ```bash
  uv run ruff format --check .
  uv run ruff check .
  uv run mypy src
  uv run pytest
  ```

## Tradeoffs

- Discarding extended-hours minutes is intentionally narrower than retaining data
  for a hypothetical future rule. A future feature should add explicit session
  meaning rather than reuse regular fast history accidentally.
- Full regular-day startup replay does more REST reading than a precise delta
  request, but it is easy to reason about and acceptable for at most 30 symbols.
- Provider `is_open` plus fixed regular boundaries do not model early closes and
  holidays perfectly. That known MVP limitation is safer to defer than adding a
  calendar dependency in this corrective slice.
- Closing at the regular-session boundary may miss a later provider revision to
  the final minute. A timed grace period is not added without live evidence that
  it materially affects alerts.
