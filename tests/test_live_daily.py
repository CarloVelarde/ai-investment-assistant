"""Tests for the after-close daily REST scan. No network."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment_assistant.clock import SteppingClock
from investment_assistant.event_manager import EventManager
from investment_assistant.live_ingest import (
    ingest_stream_payload,
    run_after_close_daily,
)
from investment_assistant.market_data import FakeMarketData, MarketSession
from investment_assistant.market_metrics import (
    RULE_MULTI_DAY_MOVE,
    RULE_RELATIVE_TO_SPY,
)
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

CLOSED_AT = datetime(2026, 2, 2, 21, 5, tzinfo=UTC)
CLOSED_SESSION = MarketSession(
    is_open=False,
    timestamp=CLOSED_AT,
    next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
    next_close=datetime(2026, 2, 3, 21, 0, tzinfo=UTC),
)
OPEN_SESSION = MarketSession(
    is_open=True,
    timestamp=datetime(2026, 2, 2, 16, 0, tzinfo=UTC),
    next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
    next_close=datetime(2026, 2, 2, 21, 0, tzinfo=UTC),
)
WATCHLIST = ("TSLA", "SPY")
PRIOR_DAYS = (
    date(2026, 1, 26),
    date(2026, 1, 27),
    date(2026, 1, 28),
    date(2026, 1, 29),
    date(2026, 1, 30),
)


def _daily(day: date, close: Decimal, *, ticker: str = "TSLA") -> MarketBar:
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


def _seed_prior_days(storage: SQLiteStorage, *, ticker: str, close: Decimal) -> None:
    for day in PRIOR_DAYS:
        storage.save_market_bar(_daily(day, close, ticker=ticker))


def _recording_researcher(
    calls: list[int],
) -> Callable[[Event, tuple[Signal, ...]], ResearchReport]:
    def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        calls.append(event.current_update)
        return create_fake_research_report(event, signals)

    return research


def _rules(storage: SQLiteStorage, event_id: str) -> set[str]:
    return {
        signal.rule
        for signal in storage.list_signals(event_id)
        if isinstance(signal, MarketSignal)
    }


def test_after_close_daily_emits_relative_to_spy_when_spy_is_present(
    tmp_path: Path,
) -> None:
    today = date(2026, 2, 2)
    provider = FakeMarketData(
        history=(
            _daily(today, Decimal("94")),
            _daily(today, Decimal("99"), ticker="SPY"),
        ),
        session=CLOSED_SESSION,
    )
    research_calls: list[int] = []
    clock = SteppingClock(CLOSED_AT)

    with SQLiteStorage(tmp_path / "daily-spy.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        _seed_prior_days(storage, ticker="TSLA", close=Decimal("100"))
        _seed_prior_days(storage, ticker="SPY", close=Decimal("100"))
        result = run_after_close_daily(
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
        rules = _rules(storage, processed[0].event_id)

    assert len(result.accepted_signal_ids) >= 1
    assert research_calls == [1]
    assert RULE_RELATIVE_TO_SPY in rules


def test_after_close_daily_skips_relative_rule_when_spy_is_missing(
    tmp_path: Path,
) -> None:
    provider = FakeMarketData(
        history=(_daily(date(2026, 2, 2), Decimal("95")),),
        session=CLOSED_SESSION,
    )
    clock = SteppingClock(CLOSED_AT)

    with SQLiteStorage(tmp_path / "daily-no-spy.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        _seed_prior_days(storage, ticker="TSLA", close=Decimal("100"))
        result = run_after_close_daily(
            storage=storage,
            manager=manager,
            provider=provider,
            watchlist=WATCHLIST,
            clock=clock,
        )
        processed = manager.process_pending(
            researcher=create_fake_research_report,
            notifier=lambda *_: None,
        )
        rules = _rules(storage, processed[0].event_id)

    assert result.accepted_signal_ids
    assert RULE_MULTI_DAY_MOVE in rules
    assert RULE_RELATIVE_TO_SPY not in rules


def test_after_close_does_not_run_while_the_session_is_open(tmp_path: Path) -> None:
    provider = FakeMarketData(
        history=(_daily(date(2026, 2, 2), Decimal("95")),),
        session=OPEN_SESSION,
    )
    clock = SteppingClock(OPEN_SESSION.timestamp)

    with SQLiteStorage(tmp_path / "still-open.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        result = run_after_close_daily(
            storage=storage,
            manager=manager,
            provider=provider,
            watchlist=WATCHLIST,
            clock=clock,
        )

        assert result.diagnostics == ("regular session still open",)
        assert storage.list_market_bars("TSLA") == ()


def test_streaming_daily_bars_do_not_run_the_daily_detector(tmp_path: Path) -> None:
    clock = SteppingClock(CLOSED_AT)
    payload = {
        "T": "d",
        "S": "TSLA",
        "o": 95,
        "h": 95,
        "l": 95,
        "c": 95,
        "v": 1_000_000,
        "t": "2026-02-02T14:30:00Z",
    }

    with SQLiteStorage(tmp_path / "running-daily.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        outcome = ingest_stream_payload(
            storage=storage,
            manager=manager,
            payload=payload,
            feed="iex",
            retrieved_at=CLOSED_AT,
            watchlist=WATCHLIST,
            now=CLOSED_AT,
        )

        assert outcome.diagnostics == ("ignored running daily bar",)
        assert storage.list_market_bars("TSLA", MarketTimeframe.ONE_DAY) == ()
        assert storage.list_events() == ()
