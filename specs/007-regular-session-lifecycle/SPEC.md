# Feature Specification: Regular Session Lifecycle Correction

**Document status:** Approved

## Purpose

Correct the remaining live-session behavior found while testing same-day and
next-day restarts. Finish this focused reliability slice before Milestone 5.

The MVP remains one readable local process. It watches regular-session prices,
recovers missed work through REST, uses the stock socket only while the regular
session is open, and lets the existing event manager decide research. This spec
does not add a general scheduler or extended-hours product.

## Problems

### Regular-session minutes are not isolated end to end

The stream path does not immediately evaluate extended-hours minutes, but it
stores them. The fast detector later reads the most recent minute rows without a
regular-session filter, so premarket or after-hours prices can change a regular
one-hour result. REST replay also sends a provider bar starting exactly at 16:00
ET to the fast detector even though regular minute starts end at 15:59 ET.

### Closed-hours recovery unnecessarily depends on the socket

A 19:00 ET restart correctly requests the full 09:30–16:00 regular day through
REST and can recover missed fast, gap, and daily signals. It then opens and may
reconnect the socket anyway. Pending recovered events are not processed until
after that connection attempt.

### A daily trigger can be lost across a date boundary

If the app is stopped before the close and first restarts the next morning or on
a weekend, the prior completed daily bar is treated as old quiet replay. Its
daily threshold crossing can be saved into detector state without ever reaching
the event manager.

### Startup has a REST-to-stream handoff window

Startup history is fetched before the socket subscribes. A minute that completes
between those actions may be absent from both the REST response and the later
stream frames.

## Scope

### In scope

- Accept and retain `1Min` bars only when their start is in the regular session:
  `09:30 <= start < 16:00` America/New_York.
- Apply the same boundary to REST replay, stream ingest, gap fill, and fast
  detector history. Existing extended-hours rows may remain in SQLite but never
  become detector inputs.
- At a same-day after-close start, REST-recover the full regular day and process
  any newly qualifying fast, session-gap, and completed daily signals without
  requiring a websocket.
- Open, consume, and reconnect the one stock websocket only while the provider’s
  regular session is open. Close it after the session closes. A closed start
  remains alive and may connect when a later session opens.
- Process durable pending events independently of socket availability.
- After an open-session subscription, run one REST minute gap fill before relying
  on stream frames.
- Quiet-replay older daily history, but allow the most recent completed daily
  session to emit once if durable detector state has not evaluated it.
- Tests with fake Alpaca, clock, time, research, and notification. No network in
  pytest.

### Out of scope

- Changes to any fast, daily, or session-gap threshold, direction, volume, rearm,
  escalation, correlation, or episode rule.
- Extended-hours, premarket, after-hours, or overnight detection and retention.
- A second socket, Alpaca overnight feed, live news, Discord, real research, or
  Milestone 5 implementation.
- Exchange-calendar integration for holidays and early closes.
- A configurable scheduler, worker, service, queue, or session policy.
- Incremental optimization of the startup history request. Re-fetching one
  regular day is acceptable for the MVP watchlist.
- A timed post-close websocket grace period for late final-minute revisions.

## Behavior

### Regular minute invariant

One helper defines whether a normalized minute starts in the regular session.
Every live path uses that invariant.

- A 15:59 ET minute may be retained and evaluated after it completes.
- A 16:00 ET, premarket, after-hours, or overnight minute is ignored with a safe
  diagnostic. It is not persisted and does not update detector state.
- Extended-hours minute rows already present from an older version are ignored
  when the fast detector selects its 61 regular-session bars.
- Daily bars are unchanged by this minute-only rule.

### Same-day after-close restart

At 19:00 ET on a trading day, startup requests:

- The existing daily lookback through now.
- The full regular minute session from 09:30 through the regular close.

Existing morning rows are idempotently upserted. Newly recovered regular minutes
replay chronologically and may emit a crossing that occurred while the app was
down. Today’s session gap may emit only if it was not evaluated earlier. Today’s
completed daily bar may run the daily rules. The event manager then applies its
existing deduplication and research policy.

No stock socket is opened or required while the provider reports the session
closed.

### Socket lifecycle and startup handoff

- When the regular session is open, keep at most one existing stock socket.
- Immediately after a successful subscription, REST-fill from the last retained
  regular minute through the current time, then consume buffered/new stream
  frames. Stable bar and signal identities keep overlaps quiet.
- Reconnect and stale-stream recovery operate only during an open regular
  session.
- When the provider reports the regular session closed, close the socket and do
  not call the stream iterator or reconnect it. Continue the process with the
  existing injected sleeper so heartbeat, daily recovery, pending events, and
  the next session still work without a busy loop.
- A socket failure does not erase or block already-durable REST signals. Pending
  work is processed without waiting for a closed-hours connection.

### Latest completed daily recovery

Startup warms detector state with older completed daily bars without emitting
them. For each ticker, the latest completed daily bar in the fetched history is
eligible for normal daily emission even when it belongs to the previous calendar
day, such as a Tuesday-morning or weekend restart after missing Monday or
Friday’s scan. It still passes through the existing detector; no catch-up signal
is invented separately.

Durable detector state and stable signal IDs make a restart on that same latest
daily bar quiet. This recovery does not late-fire an older `session_gap` or
prior-session `abrupt_move`.

### Failures

- A REST or socket failure remains visible through existing safe diagnostics and
  failure handling; credentials never appear.
- One symbol’s missing bars do not invent data or stop other symbols.
- If the latest daily bar lacks enough rule history or matching `SPY` context,
  existing detector diagnostics and skip behavior remain unchanged.

## Acceptance criteria

- [ ] AC-01: A REST or stream minute starting at 15:59 ET is retained and may
  reach the fast detector. Minutes starting at 16:00 ET or outside regular hours
  are not retained, evaluated, or written into detector state.
- [ ] AC-02: Extended-hours minute rows already stored in SQLite cannot satisfy
  the 61-bar fast lookback or change an `abrupt_move` result at the next regular
  open.
- [ ] AC-03: A 19:00 ET same-day restart REST-fills 09:30–16:00, recovers missed
  qualifying fast and completed daily signals, keeps an already-evaluated
  `session_gap` quiet, processes eligible events, and makes no socket connection.
- [ ] AC-04: A closed-session start remains running without a socket, opens one
  socket when the fake provider transitions open, and closes without reconnecting
  when the provider transitions closed.
- [ ] AC-05: A minute completed between the startup REST snapshot and socket
  subscription is recovered by one post-subscription REST gap fill. Overlap with
  a buffered stream frame does not duplicate signals or research.
- [ ] AC-06: On a next-morning or weekend restart, the latest completed daily bar
  may emit once when it has not been evaluated. A same-bar restart stays quiet,
  and prior-session fast and session-gap rules do not late-fire.
- [ ] AC-07: Pending durable event work can complete when the session is closed
  and does not depend on opening a stock socket.
- [ ] AC-08: Existing market thresholds and event-manager rules are unchanged.
  No extended-hours feature, calendar dependency, news client, Discord adapter,
  worker, or second socket is added. Ruff, mypy, and pytest pass.

## Constraints

- One local process and built-in `sqlite3` only.
- Keep provider session/time and external I/O injectable.
- Convert provider values to internal models before lifecycle or detector logic.
- Detectors emit signals only; the event manager still owns research eligibility.
- Prefer direct functions and the existing live loop over a scheduler abstraction.
- Preserve provenance for every accepted bar and signal.
- Never log secrets or use live services in pytest.

## Open questions

- None. Holidays, early closes, extended-hours products, and a post-close socket
  grace period are deliberately deferred.
