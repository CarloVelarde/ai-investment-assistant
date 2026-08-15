"""Tests for disconnect gap fill and stale-stream rules. No network."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment_assistant.clock import SteppingClock
from investment_assistant.event_manager import EventManager
from investment_assistant.live_ingest import (
    StreamHealth,
    diagnose_stream_health,
    fill_minute_gap,
    reconnect_stream,
    recover_stale_stream,
)
from investment_assistant.market_data import (
    FakeMarketData,
    MarketSession,
    StreamEventKind,
    StreamMinute,
)
from investment_assistant.models import (
    Event,
    MarketBar,
    MarketTimeframe,
    ResearchReport,
    Signal,
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


class RecordingSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


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


def _seed_crossing(storage: SQLiteStorage, manager: EventManager) -> MarketBar:
    for index in range(60):
        process_market_bar(
            storage=storage,
            manager=manager,
            bar=_minute(SESSION_OPEN + timedelta(minutes=index), Decimal("100")),
            watchlist=frozenset(WATCHLIST),
            now=SESSION_OPEN + timedelta(minutes=index + 1),
        )
    trigger = _minute(
        SESSION_OPEN + timedelta(minutes=60),
        Decimal("97"),
        volume=Decimal("2000"),
    )
    process_market_bar(
        storage=storage,
        manager=manager,
        bar=trigger,
        watchlist=frozenset(WATCHLIST),
        now=trigger.end_at,
    )
    return trigger


def _recording_researcher(
    calls: list[int],
) -> Callable[[Event, tuple[Signal, ...]], ResearchReport]:
    def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        calls.append(event.current_update)
        return create_fake_research_report(event, signals)

    return research


def test_reconnect_fills_the_gap_without_repeating_research(tmp_path: Path) -> None:
    sleeper = RecordingSleeper()
    research_calls: list[int] = []
    clock = SteppingClock(NOW)
    gap_bar = _minute(
        SESSION_OPEN + timedelta(minutes=61),
        Decimal("97"),
        volume=Decimal("2000"),
    )
    provider = FakeMarketData(history=(gap_bar,), session=SESSION)

    with SQLiteStorage(tmp_path / "reconnect.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        trigger = _seed_crossing(storage, manager)
        manager.process_pending(
            researcher=_recording_researcher(research_calls),
            notifier=lambda *_: None,
        )
        provider.disconnect()
        assert list(provider.iter_stream_minutes()) == []
        result = reconnect_stream(
            storage=storage,
            manager=manager,
            provider=provider,
            watchlist=WATCHLIST,
            clock=clock,
            sleeper=sleeper,
        )
        processed = manager.process_pending(
            researcher=_recording_researcher(research_calls),
            notifier=lambda *_: None,
        )

        assert provider.resubscribe_count == 1
        assert sleeper.delays == [1.0]
        assert storage.get_market_bar(gap_bar.bar_id) == gap_bar
        assert storage.get_market_bar(trigger.bar_id) is not None
        assert result.accepted_signal_ids == ()
        assert processed == ()
        assert research_calls == [1]


def test_gap_fill_persists_minutes_missed_during_a_disconnect(tmp_path: Path) -> None:
    clock = SteppingClock(NOW)
    first = _minute(SESSION_OPEN, Decimal("100"))
    missing = _minute(SESSION_OPEN + timedelta(minutes=1), Decimal("100"))
    provider = FakeMarketData(history=(missing,), session=SESSION)

    with SQLiteStorage(tmp_path / "gap.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        storage.save_market_bar(first)
        result = fill_minute_gap(
            storage=storage,
            manager=manager,
            provider=provider,
            watchlist=WATCHLIST,
            clock=clock,
        )

        assert missing.bar_id in result.persisted_bar_ids
        assert storage.get_market_bar(missing.bar_id) == missing


def test_socket_silence_during_regular_hours_is_stale() -> None:
    started = datetime(2026, 2, 2, 15, 0, tzinfo=UTC)
    health = StreamHealth(started_at=started)
    health.record(
        StreamMinute(
            StreamEventKind.BAR, _minute(started, Decimal("100"), ticker="SPY")
        ),
        now=started,
    )

    status = diagnose_stream_health(
        health,
        now=started + timedelta(seconds=120),
        session=SESSION,
    )

    assert status.is_stale is True
    assert status.socket_silent is True
    assert "120 seconds" in status.diagnostics[0]


def test_one_missing_illiquid_minute_is_not_a_dead_stream() -> None:
    now = datetime(2026, 2, 2, 15, 32, tzinfo=UTC)
    health = StreamHealth(started_at=SESSION_OPEN)
    health.record(
        StreamMinute(
            StreamEventKind.BAR,
            _minute(
                datetime(2026, 2, 2, 15, 31, tzinfo=UTC), Decimal("100"), ticker="SPY"
            ),
        ),
        now=now,
    )

    status = diagnose_stream_health(health, now=now, session=SESSION)

    assert status.is_stale is False
    assert status.diagnostics == ()


def test_stale_spy_backfills_without_treating_one_name_as_fatal(
    tmp_path: Path,
) -> None:
    sleeper = RecordingSleeper()
    clock = SteppingClock(datetime(2026, 2, 2, 15, 36, tzinfo=UTC))
    last_spy = _minute(
        datetime(2026, 2, 2, 15, 30, tzinfo=UTC), Decimal("100"), ticker="SPY"
    )
    filled = _minute(
        datetime(2026, 2, 2, 15, 35, tzinfo=UTC), Decimal("100"), ticker="SPY"
    )
    health = StreamHealth(started_at=SESSION_OPEN, last_message_at=clock.now())
    health.last_spy_regular_end_at = last_spy.end_at
    provider = FakeMarketData(history=(filled, last_spy), session=SESSION)

    with SQLiteStorage(tmp_path / "stale-spy.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        storage.save_market_bar(last_spy)
        result = recover_stale_stream(
            storage=storage,
            manager=manager,
            provider=provider,
            watchlist=WATCHLIST,
            clock=clock,
            health=health,
            sleeper=sleeper,
        )

        assert any("SPY" in item for item in result.diagnostics)
        assert storage.get_market_bar(filled.bar_id) == filled
        assert sleeper.delays == []
        assert provider.resubscribe_count == 0
