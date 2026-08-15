"""Tests for startup backfill and quiet replay. No network."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment_assistant.clock import SteppingClock
from investment_assistant.event_manager import EventManager
from investment_assistant.live_ingest import backfill_and_replay
from investment_assistant.market_data import (
    DAILY_BACKFILL_TRADING_DAYS,
    FakeMarketData,
    MarketSession,
    daily_backfill_start,
    live_cutoff,
    minute_backfill_range,
)
from investment_assistant.market_metrics import RULE_ABRUPT_MOVE, RULE_MULTI_DAY_MOVE
from investment_assistant.models import (
    Event,
    MarketBar,
    MarketSignal,
    MarketTimeframe,
    MarketWindow,
    ResearchReport,
    Signal,
    SignalDirection,
    SignalImportance,
    SourceDetails,
)
from investment_assistant.reporting import create_fake_research_report
from investment_assistant.storage import SQLiteStorage

NOW = datetime(2026, 2, 2, 16, 0, tzinfo=UTC)
SESSION = MarketSession(
    is_open=True,
    timestamp=NOW,
    next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
    next_close=datetime(2026, 2, 2, 21, 0, tzinfo=UTC),
)
WATCHLIST = ("TSLA", "SPY")


def _daily(
    day: date,
    close: Decimal,
    *,
    ticker: str = "TSLA",
) -> MarketBar:
    start_at = datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC)
    return MarketBar(
        ticker=ticker,
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
        retrieved_at=start_at + timedelta(hours=6, minutes=35),
    )


def _minute(
    start_at: datetime,
    close: Decimal,
    *,
    volume: Decimal = Decimal("1000"),
    ticker: str = "TSLA",
) -> MarketBar:
    return MarketBar(
        ticker=ticker,
        timeframe=MarketTimeframe.ONE_MINUTE,
        start_at=start_at,
        end_at=start_at + timedelta(minutes=1),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        is_complete=True,
        provider="alpaca",
        feed="iex",
        retrieved_at=start_at + timedelta(minutes=1),
    )


def _weekdays_ending(end: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    return days


def _recording_researcher(
    calls: list[int],
) -> Callable[[Event, tuple[Signal, ...]], ResearchReport]:
    def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        calls.append(event.current_update)
        return create_fake_research_report(event, signals)

    return research


def test_daily_backfill_start_covers_at_least_21_weekdays() -> None:
    start = daily_backfill_start(NOW)

    weekdays = 0
    cursor = start.date()
    while cursor < NOW.date():
        if cursor.weekday() < 5:
            weekdays += 1
        cursor += timedelta(days=1)

    assert weekdays >= DAILY_BACKFILL_TRADING_DAYS


def test_minute_backfill_uses_the_open_session() -> None:
    start, end = minute_backfill_range(NOW)

    assert start == datetime(2026, 2, 2, 14, 30, tzinfo=UTC)
    assert end == NOW


def test_startup_backfill_saves_21_daily_bars_without_duplicates(
    tmp_path: Path,
) -> None:
    days = _weekdays_ending(date(2026, 1, 30), DAILY_BACKFILL_TRADING_DAYS)
    history = [_daily(day, Decimal("100")) for day in days]
    provider = FakeMarketData(history=history, session=SESSION)
    database_path = tmp_path / "backfill.sqlite3"
    clock = SteppingClock(NOW)

    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        first = backfill_and_replay(
            storage=storage,
            manager=manager,
            provider=provider,
            watchlist=WATCHLIST,
            clock=clock,
        )
        second = backfill_and_replay(
            storage=storage,
            manager=manager,
            provider=provider,
            watchlist=WATCHLIST,
            clock=clock,
        )
        saved = storage.list_market_bars("TSLA", MarketTimeframe.ONE_DAY)

    assert len(first.persisted_bar_ids) == DAILY_BACKFILL_TRADING_DAYS
    assert len(saved) == DAILY_BACKFILL_TRADING_DAYS
    assert len({bar.bar_id for bar in saved}) == DAILY_BACKFILL_TRADING_DAYS
    assert second.accepted_signal_ids == ()


def test_quiet_replay_updates_state_without_research(tmp_path: Path) -> None:
    days = _weekdays_ending(date(2026, 1, 30), 6)
    history = [_daily(day, Decimal("100")) for day in days[:-1]]
    history.append(_daily(days[-1], Decimal("95")))
    provider = FakeMarketData(history=history, session=SESSION)
    research_calls: list[int] = []
    clock = SteppingClock(NOW)

    with SQLiteStorage(tmp_path / "quiet.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        result = backfill_and_replay(
            storage=storage,
            manager=manager,
            provider=provider,
            watchlist=WATCHLIST,
            clock=clock,
        )
        processed = manager.process_pending(
            researcher=_recording_researcher(research_calls),
            notifier=lambda *_: None,
        )
        state = storage.get_detector_state(
            "TSLA",
            RULE_MULTI_DAY_MOVE,
            MarketWindow.FIVE_DAYS,
            SignalDirection.DOWN,
        )

    assert all(bar.end_at < live_cutoff(NOW) for bar in history)
    assert result.accepted_signal_ids == ()
    assert processed == ()
    assert research_calls == []
    assert state is not None
    assert state.last_emitted_importance is SignalImportance.MODERATE


def test_quiet_replay_does_not_close_an_open_episode(tmp_path: Path) -> None:
    days = _weekdays_ending(date(2026, 1, 30), 6)
    history = [_daily(day, Decimal("100")) for day in days]
    provider = FakeMarketData(history=history, session=SESSION)
    clock = SteppingClock(NOW)
    open_signal = MarketSignal(
        signal_id="market-tsla-open-1",
        ticker="TSLA",
        occurred_at=datetime(2026, 1, 23, 21, 0, tzinfo=UTC),
        importance=SignalImportance.MODERATE,
        source_details=SourceDetails(
            provider="fixture",
            source="seed",
            feed="iex",
            retrieved_at=NOW,
        ),
        direction=SignalDirection.DOWN,
        rule=RULE_MULTI_DAY_MOVE,
        window=MarketWindow.FIVE_DAYS,
        price_decline_ratio=Decimal("0.05"),
        volume_ratio=Decimal("1"),
    )

    with SQLiteStorage(tmp_path / "quiet-episode.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        created = manager.handle_signal(open_signal)
        result = backfill_and_replay(
            storage=storage,
            manager=manager,
            provider=provider,
            watchlist=WATCHLIST,
            clock=clock,
        )
        events = storage.list_events()

    assert created.event is not None
    assert result.closed_event_ids == ()
    assert events[0].episode_open is True
    assert events[0].closed_at is None


def test_cutoff_or_later_qualifying_bar_can_emit(tmp_path: Path) -> None:
    session_open = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)
    history = [
        _minute(session_open + timedelta(minutes=index), Decimal("100"))
        for index in range(60)
    ]
    history.append(
        _minute(
            session_open + timedelta(minutes=60),
            Decimal("97"),
            volume=Decimal("2000"),
        )
    )
    provider = FakeMarketData(history=history, session=SESSION)
    research_calls: list[int] = []
    clock = SteppingClock(NOW)

    with SQLiteStorage(tmp_path / "live.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        result = backfill_and_replay(
            storage=storage,
            manager=manager,
            provider=provider,
            watchlist=WATCHLIST,
            clock=clock,
        )
        processed = manager.process_pending(
            researcher=_recording_researcher(research_calls),
            notifier=lambda *_: None,
        )
        events = storage.list_events()
        state = storage.get_detector_state(
            "TSLA",
            RULE_ABRUPT_MOVE,
            MarketWindow.ONE_HOUR,
            SignalDirection.DOWN,
        )

    assert history[-1].end_at >= live_cutoff(NOW)
    assert len(result.accepted_signal_ids) == 1
    assert len(processed) == 1
    assert events[0].ticker == "TSLA"
    assert research_calls == [1]
    assert state is not None
    assert state.last_emitted_importance is SignalImportance.MODERATE
