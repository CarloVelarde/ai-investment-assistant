"""Tests for the first durable SQLite layout and model mappings."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment_assistant.models import (
    Event,
    EventStatus,
    FailureStep,
    MarketSignal,
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


def test_setup_is_repeatable_and_models_survive_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "durable-state.sqlite3"

    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        storage.initialize()
        assert storage.database_version == DATABASE_VERSION

        storage.save_event(EVENT)
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

    with SQLiteStorage(database_path) as reopened:
        reopened.initialize()

        assert reopened.database_version == DATABASE_VERSION
        assert reopened.get_event(EVENT.event_id) == EVENT
        assert reopened.get_signal(MARKET_SIGNAL.signal_id) == MARKET_SIGNAL
        assert reopened.get_signal(NEWS_SIGNAL.signal_id) == NEWS_SIGNAL
        assert reopened.list_signals(EVENT.event_id) == (
            MARKET_SIGNAL,
            NEWS_SIGNAL,
        )
        assert reopened.get_report(REPORT.report_id) == REPORT
        assert reopened.list_notification_attempts(EVENT.event_id) == (ATTEMPT,)
        assert reopened.list_failures(EVENT.event_id) == (FAILURE,)


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
