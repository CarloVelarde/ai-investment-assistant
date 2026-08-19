"""Unfinished same-day daily bars must not run after-close rules. No network."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment_assistant.clock import SteppingClock
from investment_assistant.event_manager import EventManager
from investment_assistant.live_ingest import backfill_and_replay, run_after_close_daily
from investment_assistant.market_data import (
    FakeMarketData,
    MarketSession,
    with_daily_completeness,
)
from investment_assistant.market_metrics import (
    RULE_DRAWDOWN_FROM_HIGH,
    RULE_MULTI_DAY_MOVE,
    RULE_RELATIVE_TO_SPY,
)
from investment_assistant.models import (
    Event,
    MarketBar,
    MarketTimeframe,
    MarketWindow,
    ResearchReport,
    Signal,
    SignalDirection,
    SignalImportance,
)
from investment_assistant.reporting import create_fake_research_report
from investment_assistant.storage import SQLiteStorage

OPEN_AT = datetime(2026, 2, 2, 16, 0, tzinfo=UTC)
CLOSED_AT = datetime(2026, 2, 2, 21, 5, tzinfo=UTC)
TODAY = date(2026, 2, 2)
OPEN_SESSION = MarketSession(
    is_open=True,
    timestamp=OPEN_AT,
    next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
    next_close=datetime(2026, 2, 2, 21, 0, tzinfo=UTC),
)
CLOSED_SESSION = MarketSession(
    is_open=False,
    timestamp=CLOSED_AT,
    next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
    next_close=datetime(2026, 2, 3, 21, 0, tzinfo=UTC),
)


def _daily(
    day: date,
    close: Decimal,
    *,
    ticker: str = "AMD",
    retrieved_at: datetime | None = None,
    is_complete: bool = True,
) -> MarketBar:
    start_at = datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC)
    end_at = start_at + timedelta(hours=6, minutes=30)
    return MarketBar(
        ticker=ticker,
        timeframe=MarketTimeframe.ONE_DAY,
        start_at=start_at,
        end_at=end_at,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1000000"),
        is_complete=is_complete,
        provider="alpaca",
        feed="iex",
        retrieved_at=retrieved_at or (start_at + timedelta(hours=6, minutes=35)),
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


def _amd_history(*, today_close: Decimal) -> tuple[MarketBar, ...]:
    days = _weekdays_ending(TODAY, 22)
    bars: list[MarketBar] = []
    for day in days[:-2]:
        bars.append(_daily(day, Decimal("100")))
    bars.append(_daily(days[-2], Decimal("89")))
    bars.append(_daily(TODAY, today_close, retrieved_at=OPEN_AT))
    return tuple(bars)


def test_with_daily_completeness_marks_open_session_row_unfinished() -> None:
    today = _daily(TODAY, Decimal("84.21"), retrieved_at=OPEN_AT)

    unfinished = with_daily_completeness(today, as_of=OPEN_AT)
    finished = with_daily_completeness(today, as_of=CLOSED_AT)

    assert unfinished.is_complete is False
    assert finished.is_complete is True


def test_unfinished_today_daily_does_not_emit_or_save_high_state(
    tmp_path: Path,
) -> None:
    provider = FakeMarketData(
        history=_amd_history(today_close=Decimal("84.21")), session=OPEN_SESSION
    )
    research_calls: list[int] = []
    clock = SteppingClock(OPEN_AT)

    with SQLiteStorage(tmp_path / "unfinished-amd.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        result = backfill_and_replay(
            storage=storage,
            manager=manager,
            provider=provider,
            watchlist=("AMD", "SPY"),
            clock=clock,
        )
        processed = manager.process_pending(
            researcher=_recording_researcher(research_calls),
            notifier=lambda *_: None,
        )
        today_bar = next(
            bar
            for bar in storage.list_market_bars("AMD", MarketTimeframe.ONE_DAY)
            if bar.start_at.date() == TODAY
        )
        twenty_day = storage.get_detector_state(
            "AMD",
            RULE_MULTI_DAY_MOVE,
            MarketWindow.TWENTY_DAYS,
            SignalDirection.DOWN,
        )
        five_day = storage.get_detector_state(
            "AMD",
            RULE_MULTI_DAY_MOVE,
            MarketWindow.FIVE_DAYS,
            SignalDirection.DOWN,
        )
        drawdown = storage.get_detector_state(
            "AMD",
            RULE_DRAWDOWN_FROM_HIGH,
            MarketWindow.TWENTY_DAYS,
            SignalDirection.DOWN,
        )

    assert today_bar.is_complete is False
    assert today_bar.close == Decimal("84.21")
    assert result.accepted_signal_ids == ()
    assert processed == ()
    assert research_calls == []
    assert twenty_day is not None
    assert twenty_day.last_emitted_importance is SignalImportance.MODERATE
    assert five_day is not None
    assert five_day.last_emitted_importance is SignalImportance.HIGH
    assert drawdown is not None
    assert drawdown.last_emitted_importance is SignalImportance.MODERATE


def test_moved_unfinished_close_does_not_escalate_and_close_may_emit(
    tmp_path: Path,
) -> None:
    days = _weekdays_ending(TODAY, 6)
    prior = [
        bar
        for day in days[:-1]
        for bar in (
            _daily(day, Decimal("100"), ticker="TSLA"),
            _daily(day, Decimal("100"), ticker="SPY"),
        )
    ]
    first_today = (
        _daily(TODAY, Decimal("104"), ticker="TSLA", retrieved_at=OPEN_AT),
        _daily(TODAY, Decimal("100"), ticker="SPY", retrieved_at=OPEN_AT),
    )
    moved_today = (
        _daily(
            TODAY,
            Decimal("106"),
            ticker="TSLA",
            retrieved_at=OPEN_AT + timedelta(minutes=13),
        ),
        _daily(
            TODAY,
            Decimal("100"),
            ticker="SPY",
            retrieved_at=OPEN_AT + timedelta(minutes=13),
        ),
    )
    closed_today = (
        _daily(TODAY, Decimal("106"), ticker="TSLA", retrieved_at=CLOSED_AT),
        _daily(TODAY, Decimal("100"), ticker="SPY", retrieved_at=CLOSED_AT),
    )
    research_calls: list[int] = []
    open_clock = SteppingClock(OPEN_AT)

    with SQLiteStorage(tmp_path / "moved-tsla.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=open_clock)
        first = backfill_and_replay(
            storage=storage,
            manager=manager,
            provider=FakeMarketData(
                history=(*prior, *first_today),
                session=OPEN_SESSION,
            ),
            watchlist=("TSLA", "SPY"),
            clock=open_clock,
        )
        second = backfill_and_replay(
            storage=storage,
            manager=manager,
            provider=FakeMarketData(
                history=(*prior, *moved_today),
                session=OPEN_SESSION,
            ),
            watchlist=("TSLA", "SPY"),
            clock=SteppingClock(OPEN_AT),
        )
        open_processed = manager.process_pending(
            researcher=_recording_researcher(research_calls),
            notifier=lambda *_: None,
        )
        today_bar = next(
            bar
            for bar in storage.list_market_bars("TSLA", MarketTimeframe.ONE_DAY)
            if bar.start_at.date() == TODAY
        )
        closed_clock = SteppingClock(CLOSED_AT)
        closed_manager = EventManager(storage, clock=closed_clock)
        closed = run_after_close_daily(
            storage=storage,
            manager=closed_manager,
            provider=FakeMarketData(history=closed_today, session=CLOSED_SESSION),
            watchlist=("TSLA", "SPY"),
            clock=closed_clock,
        )
        closed_processed = closed_manager.process_pending(
            researcher=_recording_researcher(research_calls),
            notifier=lambda *_: None,
        )
        rules = {
            signal.rule
            for event in storage.list_events()
            for signal in storage.list_signals(event.event_id)
        }

    assert first.accepted_signal_ids == ()
    assert second.accepted_signal_ids == ()
    assert open_processed == ()
    assert today_bar.is_complete is False
    assert today_bar.close == Decimal("106")
    assert len(closed.accepted_signal_ids) >= 1
    assert len(closed_processed) == 1
    assert research_calls == [1]
    assert RULE_RELATIVE_TO_SPY in rules
