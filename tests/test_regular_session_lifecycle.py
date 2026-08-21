"""End-to-end regular-session lifecycle checks. No network."""

from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pydantic import SecretStr

from investment_assistant.clock import FixedClock, SteppingClock
from investment_assistant.config import Settings
from investment_assistant.main import main
from investment_assistant.market_data import (
    FakeMarketData,
    HistoryPage,
    MarketSession,
    StreamEventKind,
    StreamMinute,
)
from investment_assistant.market_metrics import RULE_ABRUPT_MOVE, RULE_MULTI_DAY_MOVE
from investment_assistant.models import (
    Event,
    MarketBar,
    MarketSignal,
    MarketTimeframe,
    ResearchReport,
    Signal,
)
from investment_assistant.reporting import create_fake_research_report
from investment_assistant.storage import SQLiteStorage

AFTER_CLOSE = datetime(2026, 2, 3, 0, 0, tzinfo=UTC)
CLOSED_SESSION = MarketSession(
    is_open=False,
    timestamp=AFTER_CLOSE,
    next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
    next_close=datetime(2026, 2, 3, 21, 0, tzinfo=UTC),
)
SESSION_OPEN = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)


class RecordingMarketData(FakeMarketData):
    """Fake provider with observable socket and history behavior."""

    holds_stock_stream = True

    def __init__(
        self,
        *,
        history: Sequence[MarketBar],
        session: MarketSession = CLOSED_SESSION,
    ) -> None:
        super().__init__(history=history, session=session)
        self.open_count = 0
        self.close_count = 0
        self.iterator_count = 0
        self.stock_stream_is_open = False
        self.history_requests: list[tuple[MarketTimeframe, datetime, datetime]] = []

    def fetch_history(
        self,
        *,
        symbols: Sequence[str],
        timeframe: MarketTimeframe,
        start: datetime,
        end: datetime,
        page_token: str | None = None,
    ) -> HistoryPage:
        self.history_requests.append((timeframe, start, end))
        return super().fetch_history(
            symbols=symbols,
            timeframe=timeframe,
            start=start,
            end=end,
            page_token=page_token,
        )

    def open_stock_stream(self) -> None:
        self.open_count += 1
        self.stock_stream_is_open = True

    def close_stock_stream(self) -> None:
        self.close_count += 1
        self.stock_stream_is_open = False

    def iter_stream_minutes(self) -> Iterator[StreamMinute]:
        self.iterator_count += 1
        yield from super().iter_stream_minutes()


class HandoffMarketData(RecordingMarketData):
    """Make one completed minute visible only after stream subscription."""

    def __init__(self, *, history: Sequence[MarketBar], crossing: MarketBar) -> None:
        open_session = MarketSession(
            is_open=True,
            timestamp=crossing.end_at,
            next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
            next_close=datetime(2026, 2, 2, 21, 0, tzinfo=UTC),
        )
        super().__init__(history=history, session=open_session)
        self._crossing = crossing

    def open_stock_stream(self) -> None:
        super().open_stock_stream()
        self.add_history(self._crossing)
        self.push_stream(StreamMinute(StreamEventKind.BAR, self._crossing))


def _daily(day: date, close: Decimal) -> MarketBar:
    start_at = datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC)
    return MarketBar(
        ticker="TSLA",
        timeframe=MarketTimeframe.ONE_DAY,
        start_at=start_at,
        end_at=start_at + timedelta(hours=6, minutes=30),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1000000"),
        is_complete=True,
        provider="alpaca",
        feed="iex",
        retrieved_at=start_at + timedelta(hours=6, minutes=31),
    )


def _minute(index: int, close: Decimal) -> MarketBar:
    start_at = SESSION_OPEN + timedelta(minutes=index)
    return MarketBar(
        ticker="TSLA",
        timeframe=MarketTimeframe.ONE_MINUTE,
        start_at=start_at,
        end_at=start_at + timedelta(minutes=1),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("2000") if index == 60 else Decimal("1000"),
        is_complete=True,
        provider="alpaca",
        feed="iex",
        retrieved_at=start_at + timedelta(minutes=1),
    )


def test_after_close_restart_recovers_and_processes_without_socket(
    tmp_path: Path,
) -> None:
    daily_days = (
        date(2026, 1, 26),
        date(2026, 1, 27),
        date(2026, 1, 28),
        date(2026, 1, 29),
        date(2026, 1, 30),
    )
    history = [*(_daily(day, Decimal("100")) for day in daily_days)]
    history.append(_daily(date(2026, 2, 2), Decimal("95")))
    history.extend(
        _minute(index, Decimal("97") if index == 60 else Decimal("100"))
        for index in range(61)
    )
    provider = RecordingMarketData(history=history)
    database_path = tmp_path / "after-close-recovery.sqlite3"
    research_updates: list[int] = []
    notifications: list[int] = []

    def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        research_updates.append(event.current_update)
        return create_fake_research_report(event, signals)

    result = main(
        settings=Settings(
            alpaca_api_key_id="test-key-id",
            alpaca_api_secret_key=SecretStr("test-secret"),
            watchlist="TSLA",
            database_path=database_path,
        ),
        provider=provider,
        clock=SteppingClock(AFTER_CLOSE),
        loop=False,
        sleeper=lambda _seconds: None,
        researcher=research,
        notifier=lambda event, _report: notifications.append(event.current_update),
    )

    assert result is not None
    assert provider.open_count == 0
    assert provider.close_count == 0
    assert provider.iterator_count == 0
    assert research_updates == [2]
    assert notifications == [2]
    minute_requests = [
        request
        for request in provider.history_requests
        if request[0] is MarketTimeframe.ONE_MINUTE
    ]
    assert minute_requests == [
        (
            MarketTimeframe.ONE_MINUTE,
            datetime(2026, 2, 2, 14, 30, tzinfo=UTC),
            datetime(2026, 2, 2, 21, 0, tzinfo=UTC),
        )
    ]

    with SQLiteStorage(database_path) as storage:
        events = storage.list_events()
        rules = {
            signal.rule
            for signal in storage.list_signals(events[0].event_id)
            if isinstance(signal, MarketSignal)
        }

    assert rules == {RULE_ABRUPT_MOVE, RULE_MULTI_DAY_MOVE}


def test_post_subscription_gap_fill_closes_startup_handoff_once(
    tmp_path: Path,
) -> None:
    baseline = [_minute(index, Decimal("100")) for index in range(60)]
    crossing = _minute(60, Decimal("97"))
    provider = HandoffMarketData(history=baseline, crossing=crossing)
    database_path = tmp_path / "startup-handoff.sqlite3"
    research_updates: list[int] = []
    notifications: list[int] = []

    def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        research_updates.append(event.current_update)
        return create_fake_research_report(event, signals)

    result = main(
        settings=Settings(
            alpaca_api_key_id="test-key-id",
            alpaca_api_secret_key=SecretStr("test-secret"),
            watchlist="TSLA",
            database_path=database_path,
        ),
        provider=provider,
        clock=FixedClock(crossing.end_at),
        loop=False,
        sleeper=lambda _seconds: None,
        researcher=research,
        notifier=lambda event, _report: notifications.append(event.current_update),
    )

    assert result is not None
    assert provider.open_count == 1
    assert provider.iterator_count == 1
    assert research_updates == [1]
    assert notifications == [1]
    minute_requests = [
        request
        for request in provider.history_requests
        if request[0] is MarketTimeframe.ONE_MINUTE
    ]
    assert minute_requests == [
        (
            MarketTimeframe.ONE_MINUTE,
            SESSION_OPEN,
            crossing.end_at,
        ),
        (
            MarketTimeframe.ONE_MINUTE,
            baseline[-1].end_at,
            crossing.end_at,
        ),
    ]

    with SQLiteStorage(database_path) as storage:
        bars = storage.list_market_bars("TSLA", MarketTimeframe.ONE_MINUTE)
        events = storage.list_events()
        signals = storage.list_signals(events[0].event_id)

    assert len(bars) == 61
    assert bars[-1] == crossing
    assert len(events) == 1
    assert len(signals) == 1
    assert isinstance(signals[0], MarketSignal)
    assert signals[0].rule == RULE_ABRUPT_MOVE
