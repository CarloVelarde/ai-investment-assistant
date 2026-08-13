"""Tests for open-episode grouping and daily episode close."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment_assistant.clock import FixedClock
from investment_assistant.event_manager import EventManager
from investment_assistant.market_detection import maintain_market_episodes
from investment_assistant.market_metrics import RULE_MULTI_DAY_MOVE
from investment_assistant.models import (
    DetectorState,
    Event,
    EventStatus,
    MarketSignal,
    MarketWindow,
    NewsSignal,
    ResearchReport,
    Signal,
    SignalDirection,
    SignalImportance,
    SourceDetails,
)
from investment_assistant.reporting import create_fake_research_report
from investment_assistant.storage import SQLiteStorage

SIGNAL_TIME = datetime(2026, 2, 2, 15, 30, tzinfo=UTC)
EVENT_TIME = datetime(2026, 2, 2, 16, 0, tzinfo=UTC)
CLOSE_TIME = datetime(2026, 2, 3, 21, 0, tzinfo=UTC)
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
    rule="abrupt_move",
    window=MarketWindow.ONE_HOUR,
    price_decline_ratio=Decimal("0.06"),
    volume_ratio=Decimal("1.7"),
)
LATER_MARKET = replace(
    MARKET_SIGNAL,
    signal_id="market-acme-down-2",
    occurred_at=SIGNAL_TIME + timedelta(days=10),
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


def test_closed_episode_is_not_reused_by_a_later_market_signal(tmp_path: Path) -> None:
    with SQLiteStorage(tmp_path / "new-after-close.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(EVENT_TIME))
        first = manager.handle_signal(MARKET_SIGNAL)
        assert first.event is not None
        closed = storage.close_episode(first.event.event_id, closed_at=CLOSE_TIME)

        second = manager.handle_signal(LATER_MARKET)

        assert closed is not None
        assert closed.episode_open is False
        assert closed.closed_at == CLOSE_TIME
        assert second.event is not None
        assert second.event.event_id != first.event.event_id
        assert second.event.episode_open is True
        assert len(storage.list_events()) == 2
        assert storage.find_direction_events("ACME", SignalDirection.DOWN) == (
            second.event,
        )


def test_news_does_not_reopen_a_closed_market_episode(tmp_path: Path) -> None:
    with SQLiteStorage(tmp_path / "news-after-close.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(EVENT_TIME))
        market = manager.handle_signal(MARKET_SIGNAL)
        assert market.event is not None
        storage.close_episode(market.event.event_id, closed_at=CLOSE_TIME)

        news = manager.handle_signal(NEWS_SIGNAL)

        assert news.event is not None
        assert news.event.event_id != market.event.event_id
        assert len(storage.list_events()) == 2


def test_category_match_does_not_reuse_closed_news_first_market_episode(
    tmp_path: Path,
) -> None:
    later_news = replace(
        NEWS_SIGNAL,
        signal_id="news-acme-guidance-2",
        occurred_at=SIGNAL_TIME + timedelta(days=10),
    )
    with SQLiteStorage(tmp_path / "news-first-after-close.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(EVENT_TIME))
        news_first = manager.handle_signal(NEWS_SIGNAL)
        market = manager.handle_signal(MARKET_SIGNAL)
        assert news_first.event is not None
        assert market.event is not None
        assert market.event.event_id == news_first.event.event_id
        storage.close_episode(market.event.event_id, closed_at=CLOSE_TIME)

        later = manager.handle_signal(later_news)

        assert later.event is not None
        assert later.event.event_id != market.event.event_id
        assert later.event.episode_open is True
        assert len(storage.list_events()) == 2


def test_close_does_not_create_research(tmp_path: Path) -> None:
    research_calls: list[int] = []

    def research(event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        research_calls.append(event.current_update)
        return create_fake_research_report(event, signals)

    with SQLiteStorage(tmp_path / "close-no-research.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(EVENT_TIME))
        queued = manager.handle_signal(MARKET_SIGNAL)
        assert queued.event is not None
        manager.process_pending(researcher=research, notifier=lambda *_: None)
        notified = storage.get_event(queued.event.event_id)
        assert notified is not None
        assert notified.status is EventStatus.NOTIFIED

        storage.close_episode(notified.event_id, closed_at=CLOSE_TIME)
        after_close = manager.process_pending(
            researcher=research,
            notifier=lambda *_: None,
        )
        closed = storage.get_event(notified.event_id)

    assert research_calls == [1]
    assert after_close == ()
    assert closed is not None
    assert closed.status is EventStatus.NOTIFIED
    assert closed.episode_open is False


def test_daily_maintenance_closes_only_when_all_keys_are_clear(
    tmp_path: Path,
) -> None:
    stressed = DetectorState(
        ticker="ACME",
        rule=RULE_MULTI_DAY_MOVE,
        window=MarketWindow.FIVE_DAYS,
        direction=SignalDirection.DOWN,
        last_emitted_importance=SignalImportance.HIGH,
        updated_at=EVENT_TIME,
    )
    cleared = replace(stressed, last_emitted_importance=None, updated_at=CLOSE_TIME)

    with SQLiteStorage(tmp_path / "maintain.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(EVENT_TIME))
        created = manager.handle_signal(MARKET_SIGNAL)
        assert created.event is not None
        storage.save_detector_state(stressed)

        still_open = maintain_market_episodes(storage, "ACME", now=CLOSE_TIME)
        storage.save_detector_state(cleared)
        closed = maintain_market_episodes(storage, "ACME", now=CLOSE_TIME)
        event = storage.get_event(created.event.event_id)

    assert still_open == ()
    assert [item.event_id for item in closed] == [created.event.event_id]
    assert event is not None
    assert event.episode_open is False
    assert event.status is EventStatus.QUEUED
