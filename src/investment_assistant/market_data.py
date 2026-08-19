"""Replaceable market-data boundary and Alpaca payload normalization."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from investment_assistant.models import MarketBar, MarketTimeframe

EASTERN = ZoneInfo("America/New_York")
REGULAR_SESSION_OPEN = time(9, 30)
REGULAR_SESSION_CLOSE = time(16, 0)
ALPACA_PROVIDER = "alpaca"
DAILY_BACKFILL_TRADING_DAYS = 21
# Weekdays stand in for trading days; extra days cover holidays without a calendar.
DAILY_BACKFILL_WEEKDAY_LOOKBACK = 30
MAX_HISTORY_PAGES = 1000
AlpacaFeed = Literal["iex", "sip"]


class StreamEventKind(StrEnum):
    """Completed-minute stream messages this milestone accepts."""

    BAR = "bar"
    UPDATED_BAR = "updated_bar"


class MarketDataError(Exception):
    """A market-data provider failure."""


class MarketDataPermissionError(MarketDataError):
    """The configured feed is not permitted for this account."""


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
        self._connected = True
        self.resubscribe_count = 0

    def add_history(self, *bars: MarketBar) -> None:
        """Append normalized bars to the fake history store."""

        self._history.extend(bars)

    def push_stream(self, *events: StreamMinute) -> None:
        """Append completed-minute events to the fake stream."""

        self._stream.extend(events)

    def set_session(self, session: MarketSession) -> None:
        """Replace the fake regular-session clock."""

        self._session = session

    def disconnect(self) -> None:
        """Stop yielding stream minutes until resubscribe."""

        self._connected = False

    def resubscribe(self) -> None:
        """Mark the fake stream connected again after a disconnect."""

        self._connected = True
        self.resubscribe_count += 1

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

        if self._connected:
            yield from tuple(self._stream)


def regular_session_open(when: datetime) -> datetime:
    """Return 09:30 America/New_York on the local date of ``when``."""

    local_date = _aware_utc(when, "when").astimezone(EASTERN).date()
    open_local = datetime.combine(local_date, REGULAR_SESSION_OPEN, tzinfo=EASTERN)
    return open_local.astimezone(UTC)


def regular_session_close(when: datetime) -> datetime:
    """Return 16:00 America/New_York on the local date of ``when``."""

    local_date = _aware_utc(when, "when").astimezone(EASTERN).date()
    close_local = datetime.combine(local_date, REGULAR_SESSION_CLOSE, tzinfo=EASTERN)
    return close_local.astimezone(UTC)


def live_cutoff(now: datetime) -> datetime:
    """Return today's regular open: older bars quiet-replay, later bars can emit."""

    return regular_session_open(now)


def with_daily_completeness(bar: MarketBar, *, as_of: datetime) -> MarketBar:
    """Mark a daily bar unfinished when its session has not ended yet.

    Minute bars are unchanged. A daily bar already marked unfinished stays
    unfinished even if ``as_of`` is later; callers pass a completed replacement
    when the session has closed.
    """

    if bar.timeframe is not MarketTimeframe.ONE_DAY:
        return bar
    if bar.end_at > _aware_utc(as_of, "as_of") and bar.is_complete:
        return replace(bar, is_complete=False)
    return bar


def _bar_is_finished(
    timeframe: MarketTimeframe,
    *,
    end_at: datetime,
    as_of: datetime,
) -> bool:
    if timeframe is not MarketTimeframe.ONE_DAY:
        return True
    return end_at <= _aware_utc(as_of, "as_of")


def is_regular_session_minute(start_at: datetime) -> bool:
    """Return True when a minute bar starts in the 09:30–16:00 ET session."""

    local_time = _aware_utc(start_at, "start_at").astimezone(EASTERN).time()
    return REGULAR_SESSION_OPEN <= local_time < REGULAR_SESSION_CLOSE


def daily_backfill_start(
    now: datetime,
    *,
    trading_days: int = DAILY_BACKFILL_WEEKDAY_LOOKBACK,
) -> datetime:
    """Return midnight ET the given number of weekdays before ``now``.

    This is a weekday stand-in for trading days, padded above 21 so a holiday
    week still leaves enough completed ``1Day`` bars for the twenty-day rule.
    """

    if trading_days < 1:
        raise ValueError("trading_days must be at least 1")
    cursor = _aware_utc(now, "now").astimezone(EASTERN).date()
    remaining = trading_days
    while remaining > 0:
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            remaining -= 1
    return datetime.combine(cursor, time.min, tzinfo=EASTERN).astimezone(UTC)


def last_closed_session_date(now: datetime) -> date:
    """Return the regular-session date that most recently closed."""

    local = _aware_utc(now, "now").astimezone(EASTERN)
    if local.weekday() < 5 and local.time() >= REGULAR_SESSION_CLOSE:
        return local.date()
    return _previous_weekday(local.date())


def minute_backfill_range(now: datetime) -> tuple[datetime, datetime]:
    """Return the current regular session, or the previous weekday session."""

    now_utc = _aware_utc(now, "now")
    open_today = regular_session_open(now_utc)
    close_today = regular_session_close(now_utc)
    local = now_utc.astimezone(EASTERN)
    if open_today <= now_utc <= close_today:
        return open_today, now_utc
    if now_utc > close_today and local.weekday() < 5:
        return open_today, close_today
    session_day = _previous_weekday(local.date())
    start = datetime.combine(session_day, REGULAR_SESSION_OPEN, tzinfo=EASTERN)
    end = datetime.combine(session_day, REGULAR_SESSION_CLOSE, tzinfo=EASTERN)
    return start.astimezone(UTC), end.astimezone(UTC)


def fetch_all_history(
    provider: MarketData,
    *,
    symbols: Sequence[str],
    timeframe: MarketTimeframe,
    start: datetime,
    end: datetime,
) -> tuple[MarketBar, ...]:
    """Follow ``next_page_token`` until the provider has no more bars."""

    bars: list[MarketBar] = []
    page_token: str | None = None
    for _ in range(MAX_HISTORY_PAGES):
        page = provider.fetch_history(
            symbols=symbols,
            timeframe=timeframe,
            start=start,
            end=end,
            page_token=page_token,
        )
        bars.extend(page.bars)
        if page.next_page_token is None:
            return tuple(bars)
        page_token = page.next_page_token
    raise MarketDataError("history pagination exceeded the page limit")


def _previous_weekday(day: date) -> date:
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


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
        is_complete=_bar_is_finished(timeframe, end_at=end_at, as_of=retrieved_at),
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
