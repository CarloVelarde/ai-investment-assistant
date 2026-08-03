"""Tests for durable signal acceptance and initial event grouping."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment_assistant.clock import FixedClock
from investment_assistant.event_manager import EventManager
from investment_assistant.models import (
    Event,
    EventStatus,
    MarketSignal,
    MarketWindow,
    NewsSignal,
    SignalDirection,
    SignalImportance,
    SourceDetails,
)
from investment_assistant.storage import SQLiteStorage

SIGNAL_TIME = datetime(2026, 2, 2, 15, 30, tzinfo=UTC)
EVENT_TIME = datetime(2026, 2, 2, 16, 0, tzinfo=UTC)
SOURCE_DETAILS = SourceDetails(
    provider="fixture",
    source="offline scenario",
    feed="offline-demo",
    retrieved_at=SIGNAL_TIME,
)
MARKET_SIGNAL = MarketSignal(
    signal_id="market-acme-down-1",
    ticker="ACME",
    occurred_at=SIGNAL_TIME,
    importance=SignalImportance.HIGH,
    source_details=SOURCE_DETAILS,
    direction=SignalDirection.DOWN,
    rule="abrupt-decline",
    window=MarketWindow.ONE_HOUR,
    price_decline_ratio=Decimal("0.06"),
    volume_ratio=Decimal("1.7"),
)
NEWS_SIGNAL = NewsSignal(
    signal_id="news-acme-guidance-1",
    ticker="ACME",
    occurred_at=SIGNAL_TIME + timedelta(minutes=5),
    importance=SignalImportance.HIGH,
    source_details=SOURCE_DETAILS,
    category="GUIDANCE",
    direction=SignalDirection.DOWN,
    headline="Acme lowers guidance",
    matched_phrase="lowers guidance",
)


def test_market_signal_alone_creates_one_queued_event(tmp_path: Path) -> None:
    with SQLiteStorage(tmp_path / "market-only.sqlite3") as storage:
        storage.initialize()
        result = EventManager(storage, clock=FixedClock(EVENT_TIME)).handle_signal(
            MARKET_SIGNAL
        )

        assert result.accepted is True
        assert result.event is not None
        assert result.event.status is EventStatus.QUEUED
        assert result.event.ticker == "ACME"
        assert result.event.direction is SignalDirection.DOWN
        assert result.event.category is None
        assert storage.list_events() == (result.event,)
        assert storage.list_signals(result.event.event_id) == (MARKET_SIGNAL,)


def test_news_signal_alone_creates_one_queued_event(tmp_path: Path) -> None:
    with SQLiteStorage(tmp_path / "news-only.sqlite3") as storage:
        storage.initialize()
        result = EventManager(storage, clock=FixedClock(EVENT_TIME)).handle_signal(
            NEWS_SIGNAL
        )

        assert result.accepted is True
        assert result.event is not None
        assert result.event.status is EventStatus.QUEUED
        assert result.event.direction is SignalDirection.DOWN
        assert result.event.category == "GUIDANCE"
        assert storage.list_events() == (result.event,)
        assert storage.list_signals(result.event.event_id) == (NEWS_SIGNAL,)


def test_market_and_clear_directional_news_group_into_one_event(
    tmp_path: Path,
) -> None:
    with SQLiteStorage(tmp_path / "clear-match.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(EVENT_TIME))

        market_result = manager.handle_signal(MARKET_SIGNAL)
        news_result = manager.handle_signal(NEWS_SIGNAL)

        assert market_result.event is not None
        assert news_result.event is not None
        assert news_result.event.event_id == market_result.event.event_id
        assert len(storage.list_events()) == 1
        assert storage.list_signals(news_result.event.event_id) == (
            MARKET_SIGNAL,
            NEWS_SIGNAL,
        )


def test_market_joins_directional_event_started_by_news(tmp_path: Path) -> None:
    with SQLiteStorage(tmp_path / "news-first.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(EVENT_TIME))

        news_result = manager.handle_signal(NEWS_SIGNAL)
        market_result = manager.handle_signal(MARKET_SIGNAL)

        assert news_result.event is not None
        assert market_result.event is not None
        assert market_result.event.event_id == news_result.event.event_id
        assert len(storage.list_events()) == 1
        assert storage.list_signals(market_result.event.event_id) == (
            MARKET_SIGNAL,
            NEWS_SIGNAL,
        )


def test_same_direction_market_signals_group_into_one_event(tmp_path: Path) -> None:
    later_signal = replace(
        MARKET_SIGNAL,
        signal_id="market-acme-down-2",
        occurred_at=SIGNAL_TIME + timedelta(days=5),
        window=MarketWindow.FIVE_DAYS,
    )
    with SQLiteStorage(tmp_path / "market-group.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(EVENT_TIME))

        first = manager.handle_signal(MARKET_SIGNAL)
        second = manager.handle_signal(later_signal)

        assert first.event is not None
        assert second.event is not None
        assert second.event.event_id == first.event.event_id
        assert second.event.market_windows == (
            MarketWindow.ONE_HOUR,
            MarketWindow.FIVE_DAYS,
        )
        assert len(storage.list_signals(second.event.event_id)) == 2


def test_news_uses_category_when_there_is_no_directional_market_match(
    tmp_path: Path,
) -> None:
    unknown_direction = replace(
        NEWS_SIGNAL,
        signal_id="news-acme-guidance-unknown-1",
        direction=None,
    )
    related_news = replace(
        unknown_direction,
        signal_id="news-acme-guidance-unknown-2",
        occurred_at=SIGNAL_TIME + timedelta(minutes=10),
    )
    with SQLiteStorage(tmp_path / "news-category.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(EVENT_TIME))

        first = manager.handle_signal(unknown_direction)
        second = manager.handle_signal(related_news)

        assert first.event is not None
        assert second.event is not None
        assert second.event.event_id == first.event.event_id
        assert len(storage.list_events()) == 1


def test_news_does_not_guess_between_multiple_market_events(tmp_path: Path) -> None:
    second_market = replace(
        MARKET_SIGNAL,
        signal_id="market-acme-down-2",
        occurred_at=SIGNAL_TIME + timedelta(minutes=1),
    )
    first_event = _event("event-market-1")
    second_event = _event("event-market-2")
    with SQLiteStorage(tmp_path / "ambiguous-market.sqlite3") as storage:
        storage.initialize()
        assert storage.record_signal(MARKET_SIGNAL, first_event) is True
        assert storage.record_signal(second_market, second_event) is True
        manager = EventManager(storage, clock=FixedClock(EVENT_TIME))

        result = manager.handle_signal(NEWS_SIGNAL)

        assert result.event is not None
        assert result.event.event_id == f"event:{NEWS_SIGNAL.signal_id}"
        assert result.event.category == NEWS_SIGNAL.category
        assert len(storage.list_events()) == 3


def test_same_signal_id_is_handled_only_once(tmp_path: Path) -> None:
    with SQLiteStorage(tmp_path / "duplicate.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(EVENT_TIME))

        first = manager.handle_signal(MARKET_SIGNAL)
        duplicate = manager.handle_signal(MARKET_SIGNAL)

        assert first.accepted is True
        assert duplicate.accepted is False
        assert duplicate.event is None
        assert len(storage.list_events()) == 1
        assert first.event is not None
        assert storage.list_signals(first.event.event_id) == (MARKET_SIGNAL,)


def _event(event_id: str) -> Event:
    return Event(
        event_id=event_id,
        ticker="ACME",
        direction=SignalDirection.DOWN,
        category=None,
        importance=SignalImportance.HIGH,
        market_windows=(MarketWindow.ONE_HOUR,),
        current_update=1,
        status=EventStatus.QUEUED,
        created_at=EVENT_TIME,
        updated_at=EVENT_TIME,
    )
