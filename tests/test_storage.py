"""Tests for the durable SQLite layout and model mappings."""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment_assistant.models import (
    DetectorState,
    Event,
    EventStatus,
    FailureStep,
    MarketBar,
    MarketSignal,
    MarketTimeframe,
    MarketWindow,
    NewsSignal,
    NotificationAttempt,
    ProcessingFailure,
    ResearchReport,
    SignalDirection,
    SignalImportance,
    SourceDetails,
)
from investment_assistant.storage import DATABASE_VERSION, SQLiteStorage

OCCURRED_AT = datetime(2026, 2, 2, 15, 30, tzinfo=UTC)
CREATED_AT = datetime(2026, 2, 2, 16, 0, tzinfo=UTC)
SOURCE_DETAILS = SourceDetails(
    provider="fixture-provider",
    source="offline scenario",
    feed="minute-bars",
    retrieved_at=OCCURRED_AT + timedelta(minutes=1),
)
EVENT = Event(
    event_id="event-1",
    ticker="ACME",
    direction=SignalDirection.DOWN,
    category="GUIDANCE",
    importance=SignalImportance.HIGH,
    market_windows=(MarketWindow.ONE_HOUR,),
    current_update=1,
    status=EventStatus.QUEUED,
    created_at=CREATED_AT,
    updated_at=CREATED_AT,
)
MARKET_SIGNAL = MarketSignal(
    signal_id="market-1",
    ticker="ACME",
    occurred_at=OCCURRED_AT,
    importance=SignalImportance.HIGH,
    source_details=SOURCE_DETAILS,
    direction=SignalDirection.DOWN,
    rule="abrupt-decline",
    window=MarketWindow.ONE_HOUR,
    price_decline_ratio=Decimal("0.06"),
    volume_ratio=Decimal("1.7"),
)
NEWS_SIGNAL = NewsSignal(
    signal_id="news-1",
    ticker="ACME",
    occurred_at=OCCURRED_AT + timedelta(minutes=5),
    importance=SignalImportance.MODERATE,
    source_details=SOURCE_DETAILS,
    category="GUIDANCE",
    direction=SignalDirection.DOWN,
    headline="Acme lowers guidance",
    matched_phrase="lowers guidance",
)
REPORT = ResearchReport(
    report_id="report-1",
    event_id=EVENT.event_id,
    event_update=1,
    ticker="ACME",
    event_occurred_at=OCCURRED_AT,
    created_at=CREATED_AT + timedelta(minutes=2),
    summary="Offline fake research summary.",
    is_fake=True,
)
ATTEMPT = NotificationAttempt(
    attempt_id="attempt-1",
    event_id=EVENT.event_id,
    event_update=1,
    attempted_at=CREATED_AT + timedelta(minutes=3),
    succeeded=False,
    safe_error="offline notifier unavailable",
)
FAILURE = ProcessingFailure(
    failure_id="failure-1",
    event_id=EVENT.event_id,
    event_update=1,
    step=FailureStep.NOTIFICATION,
    retryable=True,
    occurred_at=CREATED_AT + timedelta(minutes=3),
    description="offline notifier unavailable",
)
CLOSED_EVENT = replace(
    EVENT,
    event_id="event-2",
    episode_open=False,
    closed_at=CREATED_AT + timedelta(hours=2),
)
WATCHLIST_BAR = MarketBar(
    ticker="TSLA",
    timeframe=MarketTimeframe.ONE_MINUTE,
    start_at=OCCURRED_AT,
    end_at=OCCURRED_AT + timedelta(minutes=1),
    open=Decimal("250.00"),
    high=Decimal("251.00"),
    low=Decimal("249.50"),
    close=Decimal("250.25"),
    volume=Decimal("15000"),
    is_complete=True,
    provider="fixture-provider",
    feed="minute-bars",
    retrieved_at=OCCURRED_AT + timedelta(seconds=2),
)
SPY_BAR = MarketBar(
    ticker="SPY",
    timeframe=MarketTimeframe.ONE_DAY,
    start_at=datetime(2026, 2, 2, 14, 30, tzinfo=UTC),
    end_at=datetime(2026, 2, 2, 21, 0, tzinfo=UTC),
    open=Decimal("500.00"),
    high=Decimal("502.00"),
    low=Decimal("498.00"),
    close=Decimal("499.50"),
    volume=Decimal("80000000"),
    is_complete=True,
    provider="fixture-provider",
    feed="daily-bars",
    retrieved_at=datetime(2026, 2, 2, 21, 5, tzinfo=UTC),
)
DETECTOR_STATE = DetectorState(
    ticker="TSLA",
    rule="abrupt_move",
    window=MarketWindow.ONE_HOUR,
    direction=SignalDirection.DOWN,
    last_emitted_importance=SignalImportance.HIGH,
    updated_at=OCCURRED_AT,
)
CLEAR_DETECTOR_STATE = replace(
    DETECTOR_STATE,
    rule="multi_day_move",
    window=MarketWindow.FIVE_DAYS,
    last_emitted_importance=None,
)
_V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    direction TEXT,
    category TEXT,
    importance TEXT NOT NULL,
    market_windows TEXT NOT NULL,
    current_update INTEGER NOT NULL CHECK (current_update >= 1),
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_notified_at TEXT,
    CHECK (direction IS NOT NULL OR category IS NOT NULL)
);

PRAGMA user_version = 1;
"""


def test_setup_is_repeatable_and_models_survive_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "durable-state.sqlite3"

    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        storage.initialize()
        assert storage.database_version == DATABASE_VERSION

        storage.save_event(EVENT)
        storage.save_event(CLOSED_EVENT)
        storage.save_signal(
            MARKET_SIGNAL,
            event_id=EVENT.event_id,
            affected_update=1,
        )
        storage.save_signal(
            NEWS_SIGNAL,
            event_id=EVENT.event_id,
            affected_update=1,
        )
        storage.save_report(REPORT)
        storage.save_notification_attempt(ATTEMPT)
        storage.save_failure(FAILURE)
        storage.save_market_bar(WATCHLIST_BAR)
        storage.save_market_bar(SPY_BAR)
        storage.save_detector_state(DETECTOR_STATE)
        storage.save_detector_state(CLEAR_DETECTOR_STATE)

    with SQLiteStorage(database_path) as reopened:
        reopened.initialize()

        assert reopened.database_version == DATABASE_VERSION
        assert reopened.get_event(EVENT.event_id) == EVENT
        assert reopened.get_event(CLOSED_EVENT.event_id) == CLOSED_EVENT
        assert reopened.get_signal(MARKET_SIGNAL.signal_id) == MARKET_SIGNAL
        assert reopened.get_signal(NEWS_SIGNAL.signal_id) == NEWS_SIGNAL
        assert reopened.list_signals(EVENT.event_id) == (
            MARKET_SIGNAL,
            NEWS_SIGNAL,
        )
        assert reopened.get_report(REPORT.report_id) == REPORT
        assert reopened.list_notification_attempts(EVENT.event_id) == (ATTEMPT,)
        assert reopened.list_failures(EVENT.event_id) == (FAILURE,)
        assert reopened.get_market_bar(WATCHLIST_BAR.bar_id) == WATCHLIST_BAR
        assert reopened.get_market_bar(SPY_BAR.bar_id) == SPY_BAR
        assert (
            reopened.get_detector_state(
                DETECTOR_STATE.ticker,
                DETECTOR_STATE.rule,
                DETECTOR_STATE.window,
                DETECTOR_STATE.direction,
            )
            == DETECTOR_STATE
        )
        assert (
            reopened.get_detector_state(
                CLEAR_DETECTOR_STATE.ticker,
                CLEAR_DETECTOR_STATE.rule,
                CLEAR_DETECTOR_STATE.window,
                CLEAR_DETECTOR_STATE.direction,
            )
            == CLEAR_DETECTOR_STATE
        )


def test_source_details_reload_as_an_internal_model(tmp_path: Path) -> None:
    with SQLiteStorage(tmp_path / "source-details.sqlite3") as storage:
        storage.initialize()
        storage.save_event(EVENT)
        storage.save_signal(
            MARKET_SIGNAL,
            event_id=EVENT.event_id,
            affected_update=1,
        )

        reloaded = storage.get_signal(MARKET_SIGNAL.signal_id)

    assert isinstance(reloaded, MarketSignal)
    assert isinstance(reloaded.source_details, SourceDetails)
    assert reloaded.source_details == SOURCE_DETAILS


def test_version_1_database_migrates_and_keeps_events(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-v1.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(_V1_SCHEMA)
        connection.execute(
            """
            INSERT INTO events (
                event_id, ticker, direction, category, importance,
                market_windows, current_update, status, created_at,
                updated_at, last_notified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                EVENT.event_id,
                EVENT.ticker,
                EVENT.direction.value if EVENT.direction is not None else None,
                EVENT.category,
                EVENT.importance.value,
                '["ONE_HOUR"]',
                EVENT.current_update,
                EVENT.status.value,
                EVENT.created_at.isoformat(),
                EVENT.updated_at.isoformat(),
                None,
            ),
        )

    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        storage.initialize()

        assert storage.database_version == DATABASE_VERSION
        migrated = storage.get_event(EVENT.event_id)
        assert migrated == EVENT
        assert migrated is not None
        assert migrated.episode_open is True
        assert migrated.closed_at is None

        storage.save_market_bar(WATCHLIST_BAR)
        storage.save_detector_state(DETECTOR_STATE)
        assert storage.get_market_bar(WATCHLIST_BAR.bar_id) == WATCHLIST_BAR
        assert (
            storage.get_detector_state(
                DETECTOR_STATE.ticker,
                DETECTOR_STATE.rule,
                DETECTOR_STATE.window,
                DETECTOR_STATE.direction,
            )
            == DETECTOR_STATE
        )
