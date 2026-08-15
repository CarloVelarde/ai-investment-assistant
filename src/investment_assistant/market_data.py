"""Replaceable market-data boundary and Alpaca payload normalization."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from investment_assistant.models import MarketBar, MarketTimeframe

EASTERN = ZoneInfo("America/New_York")
REGULAR_SESSION_CLOSE = time(16, 0)
ALPACA_PROVIDER = "alpaca"
AlpacaFeed = Literal["iex", "sip"]


class StreamEventKind(StrEnum):
    """Completed-minute stream messages this milestone accepts."""

    BAR = "bar"
    UPDATED_BAR = "updated_bar"


class MarketDataError(Exception):
    """A market-data provider failure."""


@dataclass(frozen=True, slots=True)
class HistoryPage:
    """One page of already-normalized historical bars."""

    bars: tuple[MarketBar, ...]
    next_page_token: str | None = None


@dataclass(frozen=True, slots=True)
class MarketSession:
    """Regular-session clock state from the market-data provider."""

    is_open: bool
    timestamp: datetime
    next_open: datetime
    next_close: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _aware_utc(self.timestamp, "timestamp"))
        object.__setattr__(self, "next_open", _aware_utc(self.next_open, "next_open"))
        object.__setattr__(
            self,
            "next_close",
            _aware_utc(self.next_close, "next_close"),
        )


@dataclass(frozen=True, slots=True)
class StreamMinute:
    """A completed or late-revised minute from the stock stream."""

    kind: StreamEventKind
    bar: MarketBar


class MarketData(Protocol):
    """Provider-agnostic market history, session clock, and minute stream."""

    def fetch_history(
        self,
        *,
        symbols: Sequence[str],
        timeframe: MarketTimeframe,
        start: datetime,
        end: datetime,
        page_token: str | None = None,
    ) -> HistoryPage:
        """Return one page of completed bars in ``[start, end]``."""
        ...

    def get_session(self) -> MarketSession:
        """Return whether the regular session is open."""
        ...

    def iter_stream_minutes(self) -> Iterator[StreamMinute]:
        """Yield completed minute bars and late revisions."""
        ...


class FakeMarketData:
    """In-memory market-data provider for tests."""

    def __init__(
        self,
        *,
        history: Sequence[MarketBar] = (),
        stream: Sequence[StreamMinute] = (),
        session: MarketSession | None = None,
        page_size: int = 1000,
    ) -> None:
        if page_size < 1:
            raise ValueError("page_size must be at least 1")
        self._history = list(history)
        self._stream = list(stream)
        self._session = session
        self._page_size = page_size

    def add_history(self, *bars: MarketBar) -> None:
        """Append normalized bars to the fake history store."""

        self._history.extend(bars)

    def push_stream(self, *events: StreamMinute) -> None:
        """Append completed-minute events to the fake stream."""

        self._stream.extend(events)

    def set_session(self, session: MarketSession) -> None:
        """Replace the fake regular-session clock."""

        self._session = session

    def fetch_history(
        self,
        *,
        symbols: Sequence[str],
        timeframe: MarketTimeframe,
        start: datetime,
        end: datetime,
        page_token: str | None = None,
    ) -> HistoryPage:
        """Return one in-memory page filtered like a history request."""

        start_utc = _aware_utc(start, "start")
        end_utc = _aware_utc(end, "end")
        wanted = {symbol.strip().upper() for symbol in symbols if symbol.strip()}
        matching = [
            bar
            for bar in self._history
            if bar.ticker in wanted
            and bar.timeframe is timeframe
            and start_utc <= bar.start_at <= end_utc
        ]
        matching.sort(key=lambda bar: (bar.ticker, bar.start_at))
        offset = 0 if page_token is None else _page_offset(page_token)
        page = matching[offset : offset + self._page_size]
        next_offset = offset + len(page)
        next_token = str(next_offset) if next_offset < len(matching) else None
        return HistoryPage(bars=tuple(page), next_page_token=next_token)

    def get_session(self) -> MarketSession:
        """Return the configured session, or fail if none was set."""

        if self._session is None:
            raise MarketDataError("market session is not available")
        return self._session

    def iter_stream_minutes(self) -> Iterator[StreamMinute]:
        """Yield a snapshot of queued stream minutes."""

        yield from tuple(self._stream)


def regular_session_close(when: datetime) -> datetime:
    """Return 16:00 America/New_York on the local date of ``when``."""

    local_date = _aware_utc(when, "when").astimezone(EASTERN).date()
    close_local = datetime.combine(local_date, REGULAR_SESSION_CLOSE, tzinfo=EASTERN)
    return close_local.astimezone(UTC)


def market_bar_from_alpaca(
    payload: Mapping[str, object],
    *,
    timeframe: MarketTimeframe,
    feed: AlpacaFeed,
    retrieved_at: datetime,
    ticker: str | None = None,
) -> MarketBar:
    """Convert one Alpaca REST or stream bar mapping into a ``MarketBar``."""

    if not isinstance(payload, Mapping):
        raise TypeError("Alpaca bar payload must be a mapping")
    _reject_non_complete_stream_type(payload.get("T"))
    if feed not in ("iex", "sip"):
        raise ValueError("feed must be iex or sip")

    start_at = parse_alpaca_timestamp(payload.get("t"))
    if timeframe is MarketTimeframe.ONE_MINUTE:
        end_at = start_at + timedelta(minutes=1)
    elif timeframe is MarketTimeframe.ONE_DAY:
        end_at = regular_session_close(start_at)
    else:
        raise ValueError(f"unsupported timeframe: {timeframe}")

    return MarketBar(
        ticker=_payload_ticker(payload, ticker),
        timeframe=timeframe,
        start_at=start_at,
        end_at=end_at,
        open=_as_decimal(payload.get("o"), "o"),
        high=_as_decimal(payload.get("h"), "h"),
        low=_as_decimal(payload.get("l"), "l"),
        close=_as_decimal(payload.get("c"), "c"),
        volume=_as_decimal(payload.get("v"), "v"),
        is_complete=True,
        provider=ALPACA_PROVIDER,
        feed=feed,
        retrieved_at=retrieved_at,
    )


def stream_minute_from_alpaca(
    payload: Mapping[str, object],
    *,
    feed: AlpacaFeed,
    retrieved_at: datetime,
) -> StreamMinute:
    """Convert a completed ``bars`` or ``updatedBars`` message."""

    if not isinstance(payload, Mapping):
        raise TypeError("Alpaca stream payload must be a mapping")
    message_type = payload.get("T")
    if not isinstance(message_type, str):
        raise ValueError("stream payload must be a completed bar or updated bar")
    kind = _STREAM_KINDS.get(message_type)
    if kind is None:
        raise ValueError("stream payload must be a completed bar or updated bar")
    return StreamMinute(
        kind=kind,
        bar=market_bar_from_alpaca(
            payload,
            timeframe=MarketTimeframe.ONE_MINUTE,
            feed=feed,
            retrieved_at=retrieved_at,
        ),
    )


def parse_alpaca_timestamp(value: object) -> datetime:
    """Parse an Alpaca RFC-3339 timestamp into UTC."""

    if isinstance(value, datetime):
        return _aware_utc(value, "timestamp")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be an RFC-3339 string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = _limit_fractional_seconds(text)
    parsed = datetime.fromisoformat(text)
    return _aware_utc(parsed, "timestamp")


_STREAM_KINDS = {
    "b": StreamEventKind.BAR,
    "u": StreamEventKind.UPDATED_BAR,
}


def _reject_non_complete_stream_type(message_type: object) -> None:
    if message_type is None:
        return
    if message_type not in _STREAM_KINDS:
        raise ValueError(f"unsupported Alpaca bar message type: {message_type}")


def _payload_ticker(payload: Mapping[str, object], ticker: str | None) -> str:
    if ticker is not None and ticker.strip():
        return ticker
    symbol = payload.get("S")
    if isinstance(symbol, str) and symbol.strip():
        return symbol
    raise ValueError("ticker is required")


def _as_decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field_name} must be a number")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str) and value.strip():
        return Decimal(value.strip())
    raise ValueError(f"{field_name} must be a number")


def _limit_fractional_seconds(text: str) -> str:
    if "." not in text:
        return text
    head, rest = text.split(".", 1)
    digits = ""
    suffix = ""
    for index, char in enumerate(rest):
        if char.isdigit():
            digits += char
        else:
            suffix = rest[index:]
            break
    return f"{head}.{digits[:6].ljust(6, '0')}{suffix}"


def _page_offset(page_token: str) -> int:
    if not page_token.isdigit():
        raise ValueError("page_token is invalid")
    return int(page_token)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)
