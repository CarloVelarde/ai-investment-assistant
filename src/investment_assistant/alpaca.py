"""Alpaca REST and stock-stream adapter behind the market-data port."""

import json
import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from investment_assistant.clock import Clock
from investment_assistant.market_data import (
    AlpacaFeed,
    HistoryPage,
    MarketDataError,
    MarketDataPermissionError,
    MarketSession,
    StreamMinute,
    market_bar_from_alpaca,
    parse_alpaca_timestamp,
    stream_minute_from_alpaca,
)
from investment_assistant.models import MarketBar, MarketTimeframe
from investment_assistant.stock_stream import (
    HANDSHAKE_TIMEOUT_SECONDS,
    STREAM_RECV_TIMEOUT_SECONDS,
    StockStreamAuthError,
    StockStreamDisconnect,
    StockStreamError,
    StockStreamTransport,
    normalized_stream_symbols,
)

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
    """Normalize Alpaca REST history and stock-stream frames."""

    def __init__(
        self,
        *,
        http: HistoryHttp,
        clock: Clock,
        feed: AlpacaFeed = "iex",
        sleeper: Callable[[float], None],
        session: MarketSession | None = None,
        session_provider: Callable[[], MarketSession] | None = None,
        transport: StockStreamTransport | None = None,
        key_id: str = "",
        secret: str = "",
        symbols: Sequence[str] = (),
        recv_timeout: float = STREAM_RECV_TIMEOUT_SECONDS,
    ) -> None:
        self._http = http
        self._clock = clock
        self._feed = feed
        self._sleeper = sleeper
        self._session = session
        self._session_provider = session_provider
        self._transport = transport
        self._key_id = key_id
        self._secret = secret
        self._symbols = tuple(normalized_stream_symbols(symbols))
        self._recv_timeout = recv_timeout
        self._stream_open = False
        self._needs_reconnect = False

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
        """Return the live clock session, or an injected test session."""

        if self._session_provider is not None:
            return self._session_provider()
        if self._session is None:
            raise MarketDataError("market session is not available")
        return self._session

    @property
    def holds_stock_stream(self) -> bool:
        """Return True when a stock-stream transport is configured."""

        return self._transport is not None

    @property
    def stock_stream_url(self) -> str | None:
        """Return the configured stock websocket URL, if the transport has one."""

        return getattr(self._transport, "url", None)

    @property
    def stream_symbols(self) -> tuple[str, ...]:
        """Return the symbols that will be subscribed on the stock stream."""

        return self._symbols

    @property
    def needs_stream_reconnect(self) -> bool:
        """Return True when the live socket dropped and should be reopened."""

        return self._needs_reconnect

    def open_stock_stream(self) -> None:
        """Connect, authenticate, and subscribe. Safe to call while open."""

        if self._transport is None or self._stream_open:
            return
        if not self._symbols:
            raise MarketDataError("stock stream watchlist is empty")
        transport = self._transport
        transport.connect()
        logger.info("Stock stream connected", extra={"feed": self._feed})
        try:
            self._complete_handshake(transport)
        except Exception as error:
            self.close_stock_stream()
            if not isinstance(error, StockStreamAuthError):
                self._needs_reconnect = True
            raise
        self._stream_open = True
        self._needs_reconnect = False

    def close_stock_stream(self) -> None:
        """Close the stock websocket if a transport is configured."""

        if self._transport is not None:
            self._transport.close()
        self._stream_open = False

    def resubscribe(self) -> None:
        """Close, then connect, auth, and subscribe again."""

        self.close_stock_stream()
        self._needs_reconnect = False
        self.open_stock_stream()

    def iter_stream_minutes(self) -> Iterator[StreamMinute]:
        """Yield completed minutes from the stock stream until idle or drop."""

        transport = self._transport
        if transport is None:
            return
        try:
            self.open_stock_stream()
        except StockStreamAuthError:
            raise
        except StockStreamError:
            self._needs_reconnect = True
            return
        while True:
            try:
                frames = transport.recv_frames(timeout=self._recv_timeout)
            except StockStreamDisconnect:
                self._stream_open = False
                self._needs_reconnect = True
                return
            except StockStreamError:
                self._stream_open = False
                self._needs_reconnect = True
                return
            if not frames:
                return
            for payload in frames:
                event = self._stream_minute_or_none(payload)
                if event is not None:
                    yield event

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

    def _complete_handshake(self, transport: StockStreamTransport) -> None:
        self._expect_success(transport, "connected")
        transport.authenticate(key_id=self._key_id, secret=self._secret)
        self._expect_success(transport, "authenticated")
        transport.subscribe(self._symbols)
        self._expect_subscription(transport)

    def _expect_success(self, transport: StockStreamTransport, message: str) -> None:
        payload = self._recv_control(transport)
        if payload.get("T") == "error":
            self._raise_error_frame(payload)
        if payload.get("T") != "success" or payload.get("msg") != message:
            raise StockStreamError("unexpected stock stream handshake message")

    def _expect_subscription(self, transport: StockStreamTransport) -> None:
        payload = self._recv_control(transport)
        if payload.get("T") == "error":
            self._raise_error_frame(payload)
        if payload.get("T") != "subscription":
            raise StockStreamError("stock stream subscription was not confirmed")
        logger.info(
            "Stock stream subscribed",
            extra={
                "symbols": list(self._symbols),
                "bars": payload.get("bars"),
                "updated_bars": payload.get("updatedBars"),
            },
        )

    def _recv_control(self, transport: StockStreamTransport) -> Mapping[str, object]:
        try:
            frames = transport.recv_frames(timeout=HANDSHAKE_TIMEOUT_SECONDS)
        except StockStreamDisconnect:
            self._needs_reconnect = True
            raise
        if not frames:
            raise StockStreamError("stock stream handshake timed out")
        return frames[0]

    def _raise_error_frame(self, payload: Mapping[str, object]) -> None:
        code = payload.get("code")
        raw_message = payload.get("msg")
        text = raw_message if isinstance(raw_message, str) else "stock stream error"
        text = _without_secret(text, self._secret)
        if code in (402, 404) or "auth" in text.lower():
            raise StockStreamAuthError(
                f"Alpaca stock stream authentication failed: {text}"
            )
        raise StockStreamError(f"Alpaca stock stream error: {text}")

    def _stream_minute_or_none(
        self, payload: Mapping[str, object]
    ) -> StreamMinute | None:
        message_type = payload.get("T")
        if message_type == "d":
            return None
        if message_type not in {"b", "u"}:
            return None
        try:
            return stream_minute_from_alpaca(
                payload,
                feed=self._feed,
                retrieved_at=self._clock.now(),
            )
        except (TypeError, ValueError) as error:
            logger.warning(
                "Skipping invalid stock stream minute",
                extra={"reason": _without_secret(str(error), self._secret)},
            )
            return None


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


def _without_secret(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "***")
    return text


DATA_API_URL = "https://data.alpaca.markets"
REQUEST_TIMEOUT_SECONDS = 30


class UrllibHistoryHttp:
    """GET Alpaca stock bars with the standard library. Unused in pytest."""

    def __init__(
        self,
        *,
        key_id: str,
        secret: str,
        base_url: str = DATA_API_URL,
    ) -> None:
        self._key_id = key_id
        self._secret = secret
        self._base_url = base_url.rstrip("/")

    def get_stock_bars(self, params: Mapping[str, str]) -> HistoryHttpResponse:
        """GET /v2/stocks/bars and return status, JSON body, and headers."""

        url = f"{self._base_url}/v2/stocks/bars?{urlencode(params)}"
        return _http_get(url, key_id=self._key_id, secret=self._secret)


def fetch_alpaca_session(
    trading_url: str,
    *,
    key_id: str,
    secret: str,
) -> MarketSession:
    """Load the trading-clock session. Not used by pytest."""

    url = f"{trading_url.rstrip('/')}/v2/clock"
    response = _http_get(url, key_id=key_id, secret=secret)
    if response.status_code != 200:
        raise MarketDataError(
            f"Alpaca clock request failed with status {response.status_code}"
        )
    return MarketSession(
        is_open=bool(response.body.get("is_open")),
        timestamp=parse_alpaca_timestamp(response.body.get("timestamp")),
        next_open=parse_alpaca_timestamp(response.body.get("next_open")),
        next_close=parse_alpaca_timestamp(response.body.get("next_close")),
    )


def _http_get(url: str, *, key_id: str, secret: str) -> HistoryHttpResponse:
    request = Request(
        url,
        headers={
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8")
            parsed = json.loads(payload) if payload else {}
            if not isinstance(parsed, dict):
                raise MarketDataError("Alpaca response must be a JSON object")
            return HistoryHttpResponse(
                status_code=int(response.status),
                body=parsed,
                headers={key: str(value) for key, value in response.headers.items()},
            )
    except HTTPError as error:
        raw = error.read().decode("utf-8")
        try:
            parsed_error = json.loads(raw) if raw else {}
            body = parsed_error if isinstance(parsed_error, dict) else {}
        except json.JSONDecodeError:
            body = {}
        return HistoryHttpResponse(
            status_code=int(error.code),
            body=body,
            headers={key: str(value) for key, value in error.headers.items()},
        )
    except URLError as error:
        raise MarketDataError("Alpaca request failed") from error
    except json.JSONDecodeError as error:
        raise MarketDataError("Alpaca response was not valid JSON") from error
