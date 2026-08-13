# Feature Specification: Live Market Data

**Document status:** Approved

## Purpose

Connect a small US watchlist to Alpaca so real completed bars — not only offline fixtures — feed the Milestone 3 detectors and event manager. The app should start up, recover missing history, stay connected during regular hours, and notice the same kinds of moves it already notices in replay.

## Scope

### In scope

- Load Alpaca credentials, feed choice, and watchlist from the environment.
- Add a replaceable market-data boundary. Alpaca is the first live adapter. SDK types stay behind that boundary.
- Fetch historical `1Min` and `1Day` bars over REST, normalize them to `MarketBar`, and persist them idempotently.
- Stream completed minute bars (and late minute revisions) during regular hours.
- On startup and after a disconnect, backfill gaps from the last saved bar to now.
- After the regular session closes, fetch completed daily bars and run the existing daily detector.
- Detect a stale or dead stream, reconnect with backoff, and leave a clear diagnostic.
- Keep the offline fixture console path when live credentials are absent.
- Tests use fakes and never call Alpaca.

### Out of scope

- Live news, news websockets, or the cheap classifier (Milestone 5). The same Alpaca keys will be reused then.
- Changing Milestone 3 thresholds, rearm, episode close, or the signal contract.
- Real AI research, Discord, brokerage orders, or autonomous trading.
- Options, crypto, OTC, overnight/BOATS sessions, or after-hours-only special rules.
- User-configurable detector thresholds or scan cadences.
- A second process, worker queue, or more than one live stock stream.
- Paying for or requiring Algo Trader Plus. Basic / IEX must work. SIP is an optional config if the account has it.

## Behavior

### Credentials and mode

Live mode starts only when both Alpaca key id and secret are present and non-blank.

| Setting | Meaning |
| --- | --- |
| `INVESTMENT_ASSISTANT_ALPACA_API_KEY_ID` | Alpaca key id (`APCA-API-KEY-ID`) |
| `INVESTMENT_ASSISTANT_ALPACA_API_SECRET_KEY` | Alpaca secret (`APCA-API-SECRET-KEY`) |
| `INVESTMENT_ASSISTANT_ALPACA_FEED` | `iex` (default) or `sip` |
| `INVESTMENT_ASSISTANT_WATCHLIST` | Comma-separated tickers, for example `TSLA,AMD` |
| `INVESTMENT_ASSISTANT_ALPACA_TRADING_URL` | Trading API host for clock/calendar. Default `https://paper-api.alpaca.markets` |

Paper and live dashboard keys both talk to the **data** host `https://data.alpaca.markets`. The trading host is only for “is the market open?” Clock/calendar calls.

If keys are missing, the existing offline abrupt-drop console path still runs. Tests never require keys.

Never log, persist, or put secrets in fixtures, reports, or exceptions.

### Watchlist

Normalize tickers the same way as today (`TSLA`). Always include `SPY` even if the user omitted it. Reject a blank list. On the free Basic plan the stream allows **30 symbols**; `SPY` counts. If the list is longer than 30 including `SPY`, refuse to start live mode with a clear error.

Unknown or inactive symbols that Alpaca rejects are skipped with a diagnostic. Other symbols still run.

### What we fetch from Alpaca

**History (REST)** — `GET https://data.alpaca.markets/v2/stocks/bars`

- Timeframes: `1Min` and `1Day` only.
- `symbols`: watchlist plus `SPY`.
- `feed`: configured `iex` or `sip`.
- `adjustment`: `split` for `1Day` (multi-day math should survive splits); `raw` for `1Min`.
- Paginate with `next_page_token` until done.
- Honor `429` / rate-limit headers. Basic allows about **200 history calls per minute**.

**Live minutes (WebSocket)** — `wss://stream.data.alpaca.markets/v2/{feed}`

- Authenticate with the same key/secret.
- Subscribe the watchlist (including `SPY`) to `bars` and `updatedBars`.
- `bars` (`T=b`) are **completed** one-minute aggregates, sent after the minute mark. These drive the fast detector.
- `updatedBars` (`T=u`) are late revisions of an already-sent minute. Save them under the same bar identity and re-evaluate. Detectors stay quiet if nothing new was earned.
- Do **not** treat streaming `dailyBars` as completed daily bars. Those update every minute while the session is open.

**Market clock (Trading API)** — `GET {trading_url}/v2/clock` and `/v2/calendar`

- Used to know regular-session open/close in America/New_York.
- Fast evaluation uses only **regular-session** minute bars (09:30–16:00 ET). Extended-hours minutes may be stored but do not run the fast detector.
- After regular close, fetch completed `1Day` bars and run the daily detector.

**Not in this milestone:** news stream `wss://stream.data.alpaca.markets/v1beta1/news` and news REST. Same keys; different socket. Milestone 5.

### Normalization

Convert each Alpaca bar into `MarketBar` before storage or detection:

- ticker in standard form;
- timeframe `1Min` or `1Day`;
- `start_at` = Alpaca `t` (UTC);
- `end_at` = start plus one minute, or the regular-session close for a daily bar;
- open, high, low, close, volume;
- `is_complete = true` for REST history, stream `bars`, and stream `updatedBars`;
- `provider = alpaca`, `feed` = `iex` or `sip`, `retrieved_at` = fetch time.

Reject incomplete or invalid bars the same way Milestone 3 already does. SDK objects never enter detectors or the event manager.

### Startup

1. Load settings. If not live, keep the offline path.
2. Open SQLite and initialize.
3. Backfill `1Day` history far enough for daily rules (at least **21** trading days) and `1Min` history for the current or previous regular session.
4. Persist bars idempotently by existing bar identity.
5. **Quiet replay:** walk backfilled bars in time order and update detector state **without** sending signals to the event manager when the bar ended before the live cutoff.
6. **Live cutoff:** the start of today’s regular session. If the app starts after close, today’s completed daily bar is eligible to emit.
7. Open one stock stream, subscribe, and process new completed minutes through the existing ingest path (save → detect → handle_signal → episode maintenance).

Quiet replay exists so a restart does not research last month’s already-settled crash, but today’s open stress can still surface.

### Gaps, reconnect, and stale data

- Keep **one** stock websocket. Basic often allows only one connection to that endpoint. A second connect fails with code `406`.
- On disconnect, reconnect with bounded exponential backoff. After auth, resubscribe, then REST-backfill from each symbol’s last persisted bar end to now.
- A quiet minute on one ticker is normal on IEX (no eligible trades ⇒ no bar). That is not a dead stream.
- The stream is **stale** when, during regular hours, **no** websocket data arrives for **120 seconds**, or when `SPY` has no new completed regular-session minute for **5 minutes**. Log a diagnostic, backfill the gap, and reconnect if the socket looks dead.
- After a gap, fill missing regular-session minutes with REST. Do not invent bars.
- Insufficient SIP permission (`403` / “subscription does not permit querying recent SIP data”) is a visible configuration error, not a crash loop. IEX must still be usable.

### Daily evaluation

After the clock says regular session is closed (or at startup if we already missed today’s close):

1. Fetch completed `1Day` bars for the watchlist and `SPY`.
2. Persist them.
3. Evaluate `SPY` first when both exist for that date, then each watchlist ticker, same as Milestone 3.
4. Maintain episode close after each ticker’s daily evaluation.
5. Process pending research/notification using the existing event manager.

There is still no weekly job and no user cadence setting.

### Failures

- Bad credentials: fail live startup with a safe message; do not print the secret.
- Network errors: retry with backoff; keep already saved bars.
- One symbol failing must not stop the others.
- Detection and event rules stay deterministic and local. A live outage does not rewrite thresholds.

## Acceptance criteria

- [ ] AC-01: Live settings load key id, secret, feed, trading URL, and watchlist from the environment. Missing keys keep the offline console path. Secrets never appear in logs or fixtures.
- [ ] AC-02: An Alpaca-shaped bar (REST or stream JSON) becomes a valid `MarketBar` with provider `alpaca`, the configured feed, UTC times, and complete-bar identity. SDK types do not leak past the adapter.
- [ ] AC-03: Startup backfill saves at least 21 trading days of `1Day` bars and the needed `1Min` session bars. Repeating the same bar identity does not duplicate rows.
- [ ] AC-04: Quiet replay updates detector state for old bars without creating research. A qualifying move at or after the live cutoff still emits through the event manager.
- [ ] AC-05: A completed regular-session stream minute is persisted and runs the fast detector. A same-severity continuation stays quiet. An `updatedBars` revision replaces that minute and re-evaluates without duplicate research unless importance escalates.
- [ ] AC-06: After regular close, completed `1Day` bars run the daily detector (including relative-to-`SPY` when `SPY` is present). Streaming `dailyBars` do not trigger daily evaluation.
- [ ] AC-07: After a simulated disconnect, reconnect plus REST backfill fills the gap. Already handled crossings do not research again.
- [ ] AC-08: A stale stream during regular hours is diagnosed. A single missing IEX minute on an illiquid name is not treated as a dead stream.
- [ ] AC-09: Watchlist plus `SPY` longer than 30 symbols is rejected. Extended-hours minute bars do not run the fast detector.
- [ ] AC-10: Live mode adds no news client, Discord, workers, ORM, or weekly process. Tests use fakes and no network. Ruff, mypy, and pytest pass.

## Constraints

- Detectors still only emit signals. The event manager still owns research.
- One process. `asyncio` is allowed for the socket and timers.
- Built-in `sqlite3` only. No new database product.
- Default feed is IEX so a free Basic account works.
- Introduce `alpaca-py` only behind the adapter. Tests must not need it to talk to the network.
- Never add brokerage order endpoints.

## Open questions

- None for this draft. Feed default, daily source, regular-session filter, quiet replay cutoff, and news deferral are fixed above. Tune stale timers later only with live evidence (Milestone 8).
