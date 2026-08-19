"""Live session-open gap behavior with fake market data and clocks."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment_assistant.clock import SteppingClock
from investment_assistant.event_manager import EventManager
from investment_assistant.live_ingest import backfill_and_replay, ingest_stream_minute
from investment_assistant.market_data import (
    FakeMarketData,
    MarketSession,
    StreamEventKind,
    StreamMinute,
)
from investment_assistant.market_metrics import RULE_ABRUPT_MOVE, RULE_SESSION_GAP
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
)
from investment_assistant.reporting import create_fake_research_report
from investment_assistant.storage import SQLiteStorage

SESSION_OPEN = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)
LATE_START = datetime(2026, 2, 2, 16, 25, tzinfo=UTC)
SESSION = MarketSession(
    is_open=True,
    timestamp=LATE_START,
    next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
    next_close=datetime(2026, 2, 2, 21, 0, tzinfo=UTC),
)
WATCHLIST = ("AMD", "SPY")


def _daily(
    day: date,
    close: Decimal,
    *,
    open_price: Decimal | None = None,
    is_complete: bool = True,
) -> MarketBar:
    start_at = datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC)
    opening = close if open_price is None else open_price
    return MarketBar(
        ticker="AMD",
        timeframe=MarketTimeframe.ONE_DAY,
        start_at=start_at,
        end_at=start_at + timedelta(hours=6, minutes=30),
        open=opening,
        high=max(opening, close),
        low=min(opening, close),
        close=close,
        volume=Decimal("1000000"),
        is_complete=is_complete,
        provider="alpaca",
        feed="iex",
        retrieved_at=LATE_START,
    )


def _minute(start_at: datetime, open_price: Decimal) -> MarketBar:
    return MarketBar(
        ticker="AMD",
        timeframe=MarketTimeframe.ONE_MINUTE,
        start_at=start_at,
        end_at=start_at + timedelta(minutes=1),
        open=open_price,
        high=open_price,
        low=open_price,
        close=open_price,
        volume=Decimal("1"),
        is_complete=True,
        provider="alpaca",
        feed="iex",
        retrieved_at=start_at + timedelta(minutes=1),
    )


def _recording_researcher(
    calls: list[int],
) -> Callable[[Event, tuple[Signal, ...]], ResearchReport]:
    def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        calls.append(event.current_update)
        return create_fake_research_report(event, signals)

    return research


def test_late_start_emits_from_stored_open_without_hour_history(
    tmp_path: Path,
) -> None:
    history = (
        _daily(date(2026, 1, 30), Decimal("450")),
        _daily(
            date(2026, 2, 2),
            Decimal("999"),
            open_price=Decimal("999"),
            is_complete=False,
        ),
        _minute(SESSION_OPEN, Decimal("480")),
    )
    calls: list[int] = []
    clock = SteppingClock(LATE_START)

    with SQLiteStorage(tmp_path / "late-gap.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        result = backfill_and_replay(
            storage=storage,
            manager=manager,
            provider=FakeMarketData(history=history, session=SESSION),
            watchlist=WATCHLIST,
            clock=clock,
        )
        processed = manager.process_pending(
            researcher=_recording_researcher(calls),
            notifier=lambda *_: None,
        )
        signals = tuple(
            signal
            for event in storage.list_events()
            for signal in storage.list_signals(event.event_id)
            if isinstance(signal, MarketSignal)
        )
        fast_state = storage.get_detector_state(
            "AMD",
            RULE_ABRUPT_MOVE,
            MarketWindow.ONE_HOUR,
            SignalDirection.UP,
        )

    assert len(result.accepted_signal_ids) == 1
    assert len(processed) == 1
    assert calls == [1]
    assert len(signals) == 1
    assert signals[0].rule == RULE_SESSION_GAP
    assert signals[0].importance is SignalImportance.HIGH
    assert signals[0].baseline_price == Decimal("450")
    assert signals[0].observed_price == Decimal("480")
    assert fast_state is None


def test_same_session_restart_does_not_research_gap_again(tmp_path: Path) -> None:
    database = tmp_path / "restart-gap.sqlite3"
    history = (
        _daily(date(2026, 1, 30), Decimal("450")),
        _minute(SESSION_OPEN, Decimal("480")),
    )
    calls: list[int] = []

    with SQLiteStorage(database) as storage:
        storage.initialize()
        clock = SteppingClock(LATE_START)
        manager = EventManager(storage, clock=clock)
        first = backfill_and_replay(
            storage=storage,
            manager=manager,
            provider=FakeMarketData(history=history, session=SESSION),
            watchlist=WATCHLIST,
            clock=clock,
        )
        first_processed = manager.process_pending(
            researcher=_recording_researcher(calls),
            notifier=lambda *_: None,
        )

    with SQLiteStorage(database) as storage:
        storage.initialize()
        clock = SteppingClock(LATE_START + timedelta(minutes=5))
        manager = EventManager(storage, clock=clock)
        second = backfill_and_replay(
            storage=storage,
            manager=manager,
            provider=FakeMarketData(history=history, session=SESSION),
            watchlist=WATCHLIST,
            clock=clock,
        )
        second_processed = manager.process_pending(
            researcher=_recording_researcher(calls),
            notifier=lambda *_: None,
        )

    assert len(first.accepted_signal_ids) == 1
    assert len(first_processed) == 1
    assert second.accepted_signal_ids == ()
    assert second_processed == ()
    assert calls == [1]


def test_first_ingested_minute_uses_its_open_and_records_missing_0930(
    tmp_path: Path,
) -> None:
    first_available = _minute(SESSION_OPEN + timedelta(minutes=2), Decimal("480"))
    clock = SteppingClock(first_available.end_at)

    with SQLiteStorage(tmp_path / "missing-open-minute.sqlite3") as storage:
        storage.initialize()
        storage.save_market_bar(_daily(date(2026, 1, 30), Decimal("450")))
        manager = EventManager(storage, clock=clock)
        result = ingest_stream_minute(
            storage=storage,
            manager=manager,
            event=StreamMinute(StreamEventKind.BAR, first_available),
            watchlist=WATCHLIST,
            now=clock.now(),
        )

    assert len(result.accepted_signal_ids) == 1
    assert any("09:30 ET minute missing" in item for item in result.diagnostics)
