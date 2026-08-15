"""Alpaca REST adapter behind the market-data port. No live network in tests."""

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from investment_assistant.clock import Clock
from investment_assistant.market_data import (
    AlpacaFeed,
    HistoryPage,
    MarketDataError,
    MarketDataPermissionError,
    MarketSession,
    StreamMinute,
    market_bar_from_alpaca,
)
from investment_assistant.models import MarketBar, MarketTimeframe

logger = logging.getLogger(__name__)

MAX_HISTORY_RETRIES = 5
MAX_BACKOFF_SECONDS = 32.0
SIP_PERMISSION_TEXT = "subscription does not permit querying recent SIP data"
SIP_PERMISSION_HINT = (
    f"{SIP_PERMISSION_TEXT}; "
    "set INVESTMENT_ASSISTANT_ALPACA_FEED=iex or use an account that includes SIP"
)


@dataclass(frozen=True, slots=True)
class HistoryHttpResponse:
    """One HTTP-shaped history response used by the Alpaca adapter."""

    status_code: int
    body: Mapping[str, object]
    headers: Mapping[str, str] = field(default_factory=dict)


class HistoryHttp(Protocol):
    """Fetch one Alpaca stock-bars page. Tests inject a fake."""

    def get_stock_bars(self, params: Mapping[str, str]) -> HistoryHttpResponse:
        """GET /v2/stocks/bars with the given query parameters."""
        ...


class AlpacaMarketData:
    """Normalize Alpaca REST history pages into ``MarketBar`` values."""

    def __init__(
        self,
        *,
        http: HistoryHttp,
        clock: Clock,
        feed: AlpacaFeed = "iex",
        sleeper: Callable[[float], None],
        session: MarketSession | None = None,
        stream: Sequence[StreamMinute] = (),
    ) -> None:
        self._http = http
        self._clock = clock
        self._feed = feed
        self._sleeper = sleeper
        self._session = session
        self._stream = tuple(stream)

    def fetch_history(
        self,
        *,
        symbols: Sequence[str],
        timeframe: MarketTimeframe,
        start: datetime,
        end: datetime,
        page_token: str | None = None,
    ) -> HistoryPage:
        """Fetch one REST page, retry 429s, and map bars immediately."""

        if timeframe not in (MarketTimeframe.ONE_MINUTE, MarketTimeframe.ONE_DAY):
            raise ValueError(f"unsupported timeframe: {timeframe}")
        params = {
            "symbols": ",".join(
                symbol.strip().upper() for symbol in symbols if symbol.strip()
            ),
            "timeframe": timeframe.value,
            "start": _rfc3339(start),
            "end": _rfc3339(end),
            "feed": self._feed,
            "adjustment": ("split" if timeframe is MarketTimeframe.ONE_DAY else "raw"),
        }
        if page_token:
            params["page_token"] = page_token
        response = self._get_with_retry(params)
        if response.status_code == 403 and _is_sip_permission_error(response.body):
            logger.error(
                "Alpaca history permission denied",
                extra={"feed": self._feed, "status_code": 403},
            )
            raise MarketDataPermissionError(SIP_PERMISSION_HINT)
        if response.status_code != 200:
            raise MarketDataError(
                f"Alpaca history request failed with status {response.status_code}"
            )
        return HistoryPage(
            bars=_bars_from_response(
                response.body,
                timeframe=timeframe,
                feed=self._feed,
                retrieved_at=self._clock.now(),
            ),
            next_page_token=_next_page_token(response.body),
        )

    def get_session(self) -> MarketSession:
        """Return an injected session until the trading-clock client lands."""

        if self._session is None:
            raise MarketDataError("market session is not available")
        return self._session

    def iter_stream_minutes(self) -> Iterator[StreamMinute]:
        """Yield injected stream minutes until the websocket adapter lands."""

        yield from self._stream

    def _get_with_retry(self, params: Mapping[str, str]) -> HistoryHttpResponse:
        last_error = "Alpaca history request rate limited"
        for attempt in range(MAX_HISTORY_RETRIES):
            response = self._http.get_stock_bars(params)
            if response.status_code != 429:
                return response
            if attempt == MAX_HISTORY_RETRIES - 1:
                break
            delay = _retry_delay(response.headers, attempt)
            logger.warning(
                "Alpaca history rate limited; backing off",
                extra={"attempt": attempt + 1, "delay_seconds": delay},
            )
            self._sleeper(delay)
        raise MarketDataError(last_error)


def _bars_from_response(
    body: Mapping[str, object],
    *,
    timeframe: MarketTimeframe,
    feed: AlpacaFeed,
    retrieved_at: datetime,
) -> tuple[MarketBar, ...]:
    raw_bars = body.get("bars", {})
    if raw_bars is None:
        raw_bars = {}
    if not isinstance(raw_bars, Mapping):
        raise MarketDataError("Alpaca history bars must be an object")
    bars: list[MarketBar] = []
    for symbol, items in raw_bars.items():
        if not isinstance(symbol, str):
            continue
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            logger.warning(
                "Skipping malformed Alpaca history series",
                extra={"symbol": symbol},
            )
            continue
        for item in items:
            if not isinstance(item, Mapping):
                logger.warning(
                    "Skipping malformed Alpaca history bar",
                    extra={"symbol": symbol},
                )
                continue
            try:
                bars.append(
                    market_bar_from_alpaca(
                        item,
                        ticker=symbol,
                        timeframe=timeframe,
                        feed=feed,
                        retrieved_at=retrieved_at,
                    )
                )
            except (TypeError, ValueError) as error:
                logger.warning(
                    "Skipping invalid Alpaca history bar",
                    extra={"symbol": symbol, "reason": str(error)},
                )
    bars.sort(key=lambda bar: (bar.ticker, bar.start_at, bar.bar_id))
    return tuple(bars)


def _is_sip_permission_error(body: Mapping[str, object]) -> bool:
    message = body.get("message")
    return isinstance(message, str) and SIP_PERMISSION_TEXT.lower() in message.lower()


def _next_page_token(body: Mapping[str, object]) -> str | None:
    token = body.get("next_page_token")
    if token is None:
        return None
    if not isinstance(token, str) or not token:
        return None
    return token


def _retry_delay(headers: Mapping[str, str], attempt: int) -> float:
    retry_after = _header(headers, "Retry-After")
    if retry_after is not None and retry_after.isdigit():
        return min(float(retry_after), MAX_BACKOFF_SECONDS)
    return min(2.0**attempt, MAX_BACKOFF_SECONDS)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _rfc3339(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("history start and end must be timezone-aware")
    return value.isoformat().replace("+00:00", "Z")
