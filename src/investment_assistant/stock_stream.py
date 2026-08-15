"""Replaceable Alpaca stock-stream transport. Tests inject a fake."""

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, cast

from investment_assistant.market_data import AlpacaFeed, MarketDataError

logger = logging.getLogger(__name__)

STOCK_STREAM_PREFIX = "wss://stream.data.alpaca.markets/v2/"
HANDSHAKE_TIMEOUT_SECONDS = 10.0
STREAM_RECV_TIMEOUT_SECONDS = 5.0


class StockStreamError(MarketDataError):
    """A stock websocket failure. Messages must never include secrets."""


class StockStreamAuthError(StockStreamError):
    """Authentication failed or timed out."""


class StockStreamDisconnect(StockStreamError):
    """The stock websocket closed or dropped."""


class StreamSocket(Protocol):
    """Minimal websocket used by the production transport."""

    def send(self, data: str) -> None:
        """Send one text frame."""
        ...

    def recv(self) -> str | bytes:
        """Read one text or binary frame."""
        ...

    def settimeout(self, timeout: float | None) -> None:
        """Set the receive timeout in seconds."""
        ...

    def close(self) -> None:
        """Close the socket."""
        ...


StockStreamOpener = Callable[[str], StreamSocket]


class StockStreamTransport(Protocol):
    """One stock websocket: connect, auth, subscribe, read frames, close."""

    def connect(self) -> None:
        """Open the stock stream. Keep a single connection."""
        ...

    def authenticate(self, *, key_id: str, secret: str) -> None:
        """Send the Alpaca auth message. Do not log the secret."""
        ...

    def subscribe(self, symbols: Sequence[str]) -> None:
        """Subscribe symbols to completed ``bars`` and ``updatedBars`` only."""
        ...

    def recv_frames(
        self, *, timeout: float | None = None
    ) -> tuple[Mapping[str, object], ...]:
        """Read one websocket frame as mappings. Empty on timeout."""
        ...

    def close(self) -> None:
        """Close the socket if it is open."""
        ...


def stock_stream_url(feed: AlpacaFeed) -> str:
    """Return the one stock websocket URL for the configured feed."""

    if feed not in ("iex", "sip"):
        raise ValueError("feed must be iex or sip")
    return f"{STOCK_STREAM_PREFIX}{feed}"


def parse_stock_stream_frame(raw: str | bytes) -> tuple[Mapping[str, object], ...]:
    """Parse one Alpaca websocket text frame into message mappings."""

    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    try:
        parsed = json.loads(text) if text else []
    except json.JSONDecodeError as error:
        raise StockStreamError("stock stream frame was not valid JSON") from error
    if not isinstance(parsed, list):
        raise StockStreamError("stock stream frame must be a JSON array")
    messages: list[Mapping[str, object]] = []
    for item in parsed:
        if isinstance(item, Mapping):
            messages.append(item)
            continue
        logger.warning("Skipping malformed stock stream message")
    return tuple(messages)


def normalized_stream_symbols(symbols: Sequence[str]) -> list[str]:
    """Return unique uppercase tickers in the given order."""

    unique: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        ticker = symbol.strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        unique.append(ticker)
    return unique


class FakeStockStreamTransport:
    """In-memory stock stream. Tests never open a network socket."""

    def __init__(
        self,
        incoming: Sequence[Sequence[Mapping[str, object]]] = (),
    ) -> None:
        self._incoming: list[Sequence[Mapping[str, object]] | None] = list(incoming)
        self.sent: list[dict[str, object]] = []
        self.authenticate_calls: list[tuple[str, str]] = []
        self.subscribe_calls: list[tuple[str, ...]] = []
        self.connect_count = 0
        self.close_count = 0
        self.connected = False

    def push_frames(self, *frames: Sequence[Mapping[str, object]]) -> None:
        """Queue additional websocket frames for later reads."""

        self._incoming.extend(frames)

    def queue_disconnect(self) -> None:
        """Make the next receive raise ``StockStreamDisconnect``."""

        self._incoming.append(None)

    def connect(self) -> None:
        """Mark the fake socket connected."""

        self.connect_count += 1
        self.connected = True

    def authenticate(self, *, key_id: str, secret: str) -> None:
        """Record auth without logging the secret."""

        self._require_connected()
        self.authenticate_calls.append((key_id, secret))
        self.sent.append({"action": "auth", "key": key_id, "secret": secret})

    def subscribe(self, symbols: Sequence[str]) -> None:
        """Record a bars + updatedBars subscription."""

        self._require_connected()
        names = tuple(normalized_stream_symbols(symbols))
        self.subscribe_calls.append(names)
        self.sent.append(
            {
                "action": "subscribe",
                "bars": list(names),
                "updatedBars": list(names),
            }
        )

    def recv_frames(
        self, *, timeout: float | None = None
    ) -> tuple[Mapping[str, object], ...]:
        """Pop one queued frame, return empty on idle, or raise on disconnect."""

        del timeout
        self._require_connected()
        if not self._incoming:
            return ()
        frame = self._incoming.pop(0)
        if frame is None:
            self.connected = False
            raise StockStreamDisconnect("stock stream disconnected")
        return tuple(frame)

    def close(self) -> None:
        """Close the fake socket."""

        self.connected = False
        self.close_count += 1

    def _require_connected(self) -> None:
        if not self.connected:
            raise StockStreamError("stock stream is not connected")


class WebsocketStockStreamTransport:
    """Production transport for one Alpaca stock websocket."""

    def __init__(
        self,
        url: str,
        *,
        opener: StockStreamOpener | None = None,
    ) -> None:
        self.url = _validate_stock_stream_url(url)
        self._opener = opener or _open_websocket
        self._socket: StreamSocket | None = None

    def connect(self) -> None:
        """Open the configured stock feed. A second live connect is not created."""

        if self._socket is not None:
            return
        self._socket = self._opener(self.url)

    def authenticate(self, *, key_id: str, secret: str) -> None:
        """Send the auth action. The secret is not logged."""

        self._send({"action": "auth", "key": key_id, "secret": secret})

    def subscribe(self, symbols: Sequence[str]) -> None:
        """Subscribe only ``bars`` and ``updatedBars`` for the watchlist."""

        names = normalized_stream_symbols(symbols)
        self._send({"action": "subscribe", "bars": names, "updatedBars": names})

    def recv_frames(
        self, *, timeout: float | None = None
    ) -> tuple[Mapping[str, object], ...]:
        """Read and parse one websocket frame."""

        socket = self._require_socket()
        socket.settimeout(timeout)
        try:
            raw = socket.recv()
        except Exception as error:
            if _is_timeout(error):
                return ()
            if _is_disconnect(error):
                self.close()
                raise StockStreamDisconnect("stock stream disconnected") from error
            raise StockStreamError("stock stream receive failed") from error
        return parse_stock_stream_frame(raw)

    def close(self) -> None:
        """Close the production socket if it is open."""

        socket = self._socket
        self._socket = None
        if socket is None:
            return
        try:
            socket.close()
        except Exception:
            logger.warning("Stock stream close failed")

    def _send(self, payload: Mapping[str, object]) -> None:
        try:
            self._require_socket().send(json.dumps(payload, separators=(",", ":")))
        except Exception as error:
            if _is_disconnect(error):
                self.close()
                raise StockStreamDisconnect("stock stream disconnected") from error
            raise StockStreamError("stock stream send failed") from error

    def _require_socket(self) -> StreamSocket:
        if self._socket is None:
            raise StockStreamError("stock stream is not connected")
        return self._socket


def _validate_stock_stream_url(url: str) -> str:
    feed = ""
    if url.startswith(STOCK_STREAM_PREFIX):
        feed = url.removeprefix(STOCK_STREAM_PREFIX)
    if feed not in ("iex", "sip"):
        raise ValueError("stock stream URL must be the v2 IEX or SIP feed")
    return url


def _open_websocket(url: str) -> StreamSocket:
    try:
        from websocket import create_connection
    except ImportError as error:
        raise StockStreamError(
            "websocket-client is required for the live stock stream"
        ) from error
    return cast(
        StreamSocket,
        create_connection(url, timeout=HANDSHAKE_TIMEOUT_SECONDS),
    )


def _is_timeout(error: BaseException) -> bool:
    return isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower()


def _is_disconnect(error: BaseException) -> bool:
    if isinstance(error, ConnectionError):
        return True
    name = type(error).__name__.lower()
    return "closed" in name or "disconnect" in name
