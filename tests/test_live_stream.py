"""Tests for completed stream minutes and late revisions. No network."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment_assistant.clock import SteppingClock
from investment_assistant.event_manager import EventManager
from investment_assistant.live_ingest import (
    ingest_stream_minute,
    ingest_stream_minutes,
    ingest_stream_payload,
)
from investment_assistant.market_data import (
    FakeMarketData,
    MarketSession,
    StreamEventKind,
    StreamMinute,
    is_regular_session_minute,
)
from investment_assistant.models import (
    Event,
    MarketBar,
    MarketTimeframe,
    MarketWindow,
    ResearchReport,
    Signal,
    SignalDirection,
)
from investment_assistant.pipeline import process_market_bar
from investment_assistant.reporting import create_fake_research_report
from investment_assistant.storage import SQLiteStorage

SESSION_OPEN = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)
NOW = datetime(2026, 2, 2, 16, 0, tzinfo=UTC)
WATCHLIST = ("TSLA", "SPY")
SESSION = MarketSession(
    is_open=True,
    timestamp=NOW,
    next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
    next_close=datetime(2026, 2, 2, 21, 0, tzinfo=UTC),
)


def _minute(
    start_at: datetime,
    close: Decimal,
    *,
    volume: Decimal = Decimal("1000"),
    high: Decimal | None = None,
    low: Decimal | None = None,
) -> MarketBar:
    high_price = close if high is None else high
    low_price = close if low is None else low
    return MarketBar(
        ticker="TSLA",
        timeframe=MarketTimeframe.ONE_MINUTE,
        start_at=start_at,
        end_at=start_at + timedelta(minutes=1),
        open=close,
        high=high_price,
        low=low_price,
        close=close,
        volume=volume,
        is_complete=True,
        provider="alpaca",
        feed="iex",
        retrieved_at=start_at + timedelta(minutes=1),
    )


def _seed_baseline(storage: SQLiteStorage, manager: EventManager) -> None:
    for index in range(60):
        process_market_bar(
            storage=storage,
            manager=manager,
            bar=_minute(SESSION_OPEN + timedelta(minutes=index), Decimal("100")),
            watchlist=frozenset(WATCHLIST),
            now=SESSION_OPEN + timedelta(minutes=index + 1),
        )


def _recording_researcher(
    calls: list[int],
) -> Callable[[Event, tuple[Signal, ...]], ResearchReport]:
    def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        calls.append(event.current_update)
        return create_fake_research_report(event, signals)

    return research


def test_regular_session_filter_matches_eastern_hours() -> None:
    assert is_regular_session_minute(SESSION_OPEN) is True
    assert is_regular_session_minute(datetime(2026, 2, 2, 20, 59, tzinfo=UTC)) is True
    assert is_regular_session_minute(datetime(2026, 2, 2, 21, 0, tzinfo=UTC)) is False
    assert is_regular_session_minute(datetime(2026, 2, 2, 14, 0, tzinfo=UTC)) is False


def test_extended_hours_minute_is_ignored(tmp_path: Path) -> None:
    clock = SteppingClock(NOW)
    premarket = _minute(datetime(2026, 2, 2, 14, 0, tzinfo=UTC), Decimal("90"))

    with SQLiteStorage(tmp_path / "extended.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        outcome = ingest_stream_minute(
            storage=storage,
            manager=manager,
            event=StreamMinute(StreamEventKind.BAR, premarket),
            watchlist=WATCHLIST,
            now=premarket.end_at,
        )
        processed = manager.process_pending(
            researcher=create_fake_research_report,
            notifier=lambda *_: None,
        )

        assert outcome.accepted_signal_ids == ()
        assert outcome.diagnostics == (
            f"ignored non-regular minute {premarket.bar_id}",
        )
        assert storage.get_market_bar(premarket.bar_id) is None
        assert processed == ()
        assert storage.list_events() == ()


def test_stream_retains_1559_but_ignores_1600_eastern(tmp_path: Path) -> None:
    clock = SteppingClock(datetime(2026, 2, 2, 21, 1, tzinfo=UTC))
    last_regular = _minute(datetime(2026, 2, 2, 20, 59, tzinfo=UTC), Decimal("100"))
    at_close = _minute(datetime(2026, 2, 2, 21, 0, tzinfo=UTC), Decimal("90"))
    provider = FakeMarketData(
        stream=(
            StreamMinute(StreamEventKind.BAR, last_regular),
            StreamMinute(StreamEventKind.BAR, at_close),
        ),
        session=SESSION,
    )

    with SQLiteStorage(tmp_path / "stream-boundary.sqlite3") as storage:
        storage.initialize()
        result = ingest_stream_minutes(
            storage=storage,
            manager=EventManager(storage, clock=clock),
            provider=provider,
            watchlist=WATCHLIST,
            clock=clock,
        )

        assert result.persisted_bar_ids == (last_regular.bar_id,)
        assert storage.get_market_bar(last_regular.bar_id) == last_regular
        assert storage.get_market_bar(at_close.bar_id) is None


def test_first_crossing_emits_and_continuation_stays_quiet(tmp_path: Path) -> None:
    clock = SteppingClock(NOW)
    first = _minute(
        SESSION_OPEN + timedelta(minutes=60),
        Decimal("97"),
        volume=Decimal("2000"),
    )
    continuation = _minute(
        SESSION_OPEN + timedelta(minutes=61),
        Decimal("97"),
        volume=Decimal("2000"),
    )
    provider = FakeMarketData(
        stream=(
            StreamMinute(StreamEventKind.BAR, first),
            StreamMinute(StreamEventKind.BAR, continuation),
        ),
        session=SESSION,
    )
    research_calls: list[int] = []

    with SQLiteStorage(tmp_path / "stream.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        _seed_baseline(storage, manager)
        result = ingest_stream_minutes(
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

    assert result.accepted_signal_ids == (result.accepted_signal_ids[0],)
    assert len(result.accepted_signal_ids) == 1
    assert len(processed) == 1
    assert research_calls == [1]


def test_updated_bar_replaces_and_can_escalate(tmp_path: Path) -> None:
    clock = SteppingClock(NOW)
    start_at = SESSION_OPEN + timedelta(minutes=60)
    first = _minute(start_at, Decimal("97"), volume=Decimal("2000"))
    revision = _minute(
        start_at,
        Decimal("95"),
        volume=Decimal("2000"),
        high=Decimal("97"),
        low=Decimal("95"),
    )
    revision = MarketBar(
        ticker=revision.ticker,
        timeframe=revision.timeframe,
        start_at=revision.start_at,
        end_at=revision.end_at,
        open=Decimal("97"),
        high=Decimal("97"),
        low=Decimal("95"),
        close=Decimal("95"),
        volume=Decimal("2000"),
        is_complete=True,
        provider="alpaca",
        feed="iex",
        retrieved_at=first.retrieved_at + timedelta(seconds=30),
    )
    research_calls: list[int] = []

    with SQLiteStorage(tmp_path / "revision.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        _seed_baseline(storage, manager)
        ingest_stream_minute(
            storage=storage,
            manager=manager,
            event=StreamMinute(StreamEventKind.BAR, first),
            watchlist=WATCHLIST,
            now=first.end_at,
        )
        ingest_stream_minute(
            storage=storage,
            manager=manager,
            event=StreamMinute(StreamEventKind.UPDATED_BAR, revision),
            watchlist=WATCHLIST,
            now=revision.retrieved_at,
        )
        processed = manager.process_pending(
            researcher=_recording_researcher(research_calls),
            notifier=lambda *_: None,
        )
        saved = storage.get_market_bar(first.bar_id)

    assert saved is not None
    assert saved.close == Decimal("95")
    assert saved.bar_id == first.bar_id
    assert len(processed) == 1
    assert research_calls == [2]
    assert processed[0].current_update == 2
    assert processed[0].market_windows == (MarketWindow.ONE_HOUR,)
    assert processed[0].direction is SignalDirection.DOWN


def test_running_daily_stream_bar_is_ignored(tmp_path: Path) -> None:
    clock = SteppingClock(NOW)
    payload = {
        "T": "d",
        "S": "TSLA",
        "o": 100,
        "h": 100,
        "l": 100,
        "c": 100,
        "v": 1000,
        "t": "2026-02-02T14:30:00Z",
    }

    with SQLiteStorage(tmp_path / "daily-stream.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        outcome = ingest_stream_payload(
            storage=storage,
            manager=manager,
            payload=payload,
            feed="iex",
            retrieved_at=NOW,
            watchlist=WATCHLIST,
            now=NOW,
        )

        assert outcome.diagnostics == ("ignored running daily bar",)
        assert storage.list_market_bars("TSLA") == ()
        assert storage.list_events() == ()
