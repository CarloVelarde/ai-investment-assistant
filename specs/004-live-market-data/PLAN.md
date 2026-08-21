# Implementation Plan: Live Market Data

**Document status:** Approved

## Approach

Add a thin Alpaca adapter that produces the same `MarketBar` values Milestone 3 already stores and evaluates. Keep detectors, episode close, fake research, and console notification unchanged. Offline fixtures stay the no-key path.

Work in slices: settings and watchlist, pure normalization, fake port, REST backfill, quiet replay vs live emit, stream **ingest**, after-close daily, reconnect/stale behavior, **then the real stock websocket**.

### Socket slice (done)

The ingest path, one stock websocket, and fake-transport tests are in. Do not rebuild them.

### Follow-up (do this next)

Unfinished daily prices firing after-close rules, and the missing last-close-to-this-open check, are [spec 006](../006-live-session-hardening/PLAN.md). Not more tasks on this plan.

## Alpaca facts (checked August 2026)

These are provider facts, not product rules. Recheck if Alpaca changes plans.

### Two hosts

| Job | Host |
| --- | --- |
| History and stock stream | `https://data.alpaca.markets` / `wss://stream.data.alpaca.markets` |
| Clock and calendar | Trading API: paper `https://paper-api.alpaca.markets` or live `https://api.alpaca.markets` |

Paper keys are fine for market data. Do not call trading order APIs.

Auth headers: `APCA-API-KEY-ID`, `APCA-API-SECRET-KEY`. Stream auth may also send `{"action":"auth","key","secret"}` within 10 seconds.

Official SDK: [`alpaca-py`](https://github.com/alpacahq/alpaca-py) (`StockHistoricalDataClient`, `StockDataStream`). Convert SDK objects to dicts/`MarketBar` immediately.

Docs: [About Market Data](https://docs.alpaca.markets/docs/about-market-data-api), [Historical stocks](https://docs.alpaca.markets/us/docs/historical-stock-data-1), [Stock stream](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data), [Bars REST](https://docs.alpaca.markets/us/reference/stockbars), [FAQ](https://docs.alpaca.markets/us/docs/market-data-faq).

### Plans that matter

**Basic (free, default):**

- Live equities = **IEX** only (~2.5% of volume).
- Stream: **30 symbols**, often **1 connection**.
- History: since 2016, about **200 calls/min**.
- Recent **SIP** (last 15 minutes) is forbidden without Algo Trader Plus.

**Algo Trader Plus ($99/mo, optional):**

- Live **SIP** (all US exchanges).
- Unlimited stream symbols, 10,000 history calls/min.

This milestone must run on Basic/IEX. `sip` is config for accounts that have it.

IEX can skip a minute when there is no eligible trade. No bar ≠ crashed stream. Use `SPY` plus socket silence for health.

### What each stream channel is

| Channel | Use in this milestone |
| --- | --- |
| `bars` | Yes. Completed 1-minute bars. Fast detector. |
| `updatedBars` | Yes. Late fix to a minute already sent. Replace and re-evaluate. |
| `dailyBars` | No. These are **running** day bars, sent every minute after the open. Not a completed `1Day`. |
| trades/quotes/statuses | No. |

Completed daily bars come from REST `timeframe=1Day` after the regular close.

### News (Milestone 5 only)

At the time of this completed plan, Milestone 5 was expected to use the separate
news socket at `wss://stream.data.alpaca.markets/v1beta1/news` alongside REST.
Do not open that socket here. [D-029](../../docs/DECISIONS.md) and
[spec 008](../008-live-news-classification/PLAN.md) later selected REST-only news
for the MVP and deferred the news socket as a possible later optimization.

## Key decisions

### Boundary

```text
Alpaca REST/WS
    → adapter (normalize, no SDK types leave here)
    → MarketBar
    → existing storage + detect_fast/daily + EventManager
```

One protocol, two implementations: `AlpacaMarketData` and `FakeMarketData`.

### Settings

Add optional fields to the existing Pydantic settings (prefix `INVESTMENT_ASSISTANT_`). Ship a `.env.example` with blank keys. Do not commit `.env`.

Watchlist from env is enough. Always add `SPY`. No watchlist UI.

### Regular session only for fast rules

Filter 1Min evaluation to 09:30–16:00 America/New_York. Alpaca minute bars also aggregate extended-hours trades; those must not drive `abrupt_move`. Daily REST bars already follow SIP daily rules (extended-hours trades do not set daily OHLC).

### Quiet replay

Backfill can be large. For each backfilled bar older than today’s regular open, persist and update detector state only. Do not `handle_signal`. Bars at or after that cutoff go through the live path.

This uses Milestone 3 detector state so settled crossings do not re-fire on restart.

### After-close daily

Poll Alpaca clock (or a testable `MarketSession` port). When the session flips to closed, or at live startup after close, pull `1Day` bars and run `detect_daily_from_storage` + `maintain_market_episodes` + `process_pending`.

### Health

In-memory last-message time and last `SPY` regular minute. Structured logs for connect, subscribe, stale, reconnect, backfill range, and permission errors. No new health microservice.

### Schema

No new product database. Optional tiny `market_sync` row (last successful backfill time per ticker/timeframe) is allowed if it keeps gap fill simple. Prefer “last persisted bar end” from `market_bars` first.

## Data flow

```text
env keys + watchlist
    → REST backfill 1Day + 1Min
    → persist
    → quiet detector replay (old bars)
    → one IEX/SIP websocket (bars + updatedBars)
    → regular-session 1Min → fast detector → handle_signal
    → clock says closed → REST 1Day → daily detector → episode close
    → process_pending (fake research + console)
```

Disconnect: backoff → connect → auth → subscribe → REST gap fill → continue.

## Validation

- Unit tests for settings, watchlist/`SPY`/30-symbol cap, bar mapping, regular-session filter.
- Fake REST pagination, 429 retry, SIP 403 diagnostic.
- Fake stream: first minute emits, continuation quiet, `updatedBars` replaces, disconnect gap does not duplicate research.
- Fake **socket transport**: connect/auth/subscribe sequence, `T=b`/`T=u` frames reach ingest, `T=d` ignored, auth failure is a safe diagnostic. No live Alpaca in CI.
- After-close daily with `SPY` present and missing.
- Quiet replay does not notify; cutoff bar can notify.
- Offline console still works without keys.
- Full repo checks: ruff, mypy, pytest. No live Alpaca in CI.

## Tradeoffs

- IEX is cheaper and incomplete. Some names will have sparse minutes. That is accepted for the free plan; SIP can be turned on later without changing detectors.
- Ignoring stream `dailyBars` means daily signals wait until after close. That matches Milestone 3’s “one after-close scan.”
- Quiet replay needs a silent detector path so we do not flood research on first live start.
- `updatedBars` can change a minute after we already evaluated it. Re-evaluate is simpler and safer than ignoring late tape.

## Intentional limits

- No news.
- No Discord.
- No overnight/BOATS.
- No second websocket for stocks.
- The live loop must actually open the one stock socket; an empty iterator is not the product.
- Stale timers are fixed constants until Milestone 8 has replay evidence.
