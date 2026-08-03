"""Restart tests for durable event processing stages."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from investment_assistant.clock import FixedClock
from investment_assistant.event_manager import EventManager
from investment_assistant.models import (
    Event,
    EventStatus,
    FailureStep,
    MarketSignal,
    MarketWindow,
    ResearchReport,
    Signal,
    SignalDirection,
    SignalImportance,
    SourceDetails,
)
from investment_assistant.reporting import create_fake_durable_research_report
from investment_assistant.storage import SQLiteStorage

SIGNAL_TIME = datetime(2026, 2, 4, 15, 30, tzinfo=UTC)
PROCESS_TIME = datetime(2026, 2, 4, 16, 0, tzinfo=UTC)
MARKET_SIGNAL = MarketSignal(
    signal_id="market-acme-down-1",
    ticker="ACME",
    occurred_at=SIGNAL_TIME,
    importance=SignalImportance.HIGH,
    source_details=SourceDetails(
        provider="fixture",
        source="offline scenario",
        feed="offline-demo",
        retrieved_at=SIGNAL_TIME,
    ),
    direction=SignalDirection.DOWN,
    rule="abrupt-decline",
    window=MarketWindow.ONE_HOUR,
    price_decline_ratio=Decimal("0.06"),
    volume_ratio=Decimal("1.7"),
)


def test_queued_research_continues_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "queued-restart.sqlite3"
    event_id = _save_queued_event(database_path)
    research_calls: list[int] = []
    notification_calls: list[int] = []

    with SQLiteStorage(database_path) as reopened:
        reopened.initialize()
        manager = EventManager(reopened, clock=FixedClock(PROCESS_TIME))
        completed = manager.process_pending(
            researcher=_recording_researcher(research_calls),
            notifier=_recording_notifier(notification_calls),
        )[0]

        assert completed.event_id == event_id
        assert completed.status is EventStatus.NOTIFIED
        assert research_calls == [1]
        assert notification_calls == [1]


def test_interrupted_research_restarts_after_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "researching-restart.sqlite3"
    event_id = _save_queued_event(database_path)
    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        researching = storage.mark_researching(
            event_id,
            1,
            updated_at=PROCESS_TIME,
        )
        assert researching is not None
        assert researching.status is EventStatus.RESEARCHING

    research_calls: list[int] = []
    notification_calls: list[int] = []
    with SQLiteStorage(database_path) as reopened:
        reopened.initialize()
        completed = EventManager(
            reopened,
            clock=FixedClock(PROCESS_TIME),
        ).process_pending(
            researcher=_recording_researcher(research_calls),
            notifier=_recording_notifier(notification_calls),
        )[0]

        assert completed.status is EventStatus.NOTIFIED
        assert research_calls == [1]
        assert notification_calls == [1]


def test_saved_report_resumes_at_notification_without_research(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "reported-restart.sqlite3"
    event_id = _save_queued_event(database_path)
    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        researching = storage.mark_researching(
            event_id,
            1,
            updated_at=PROCESS_TIME,
        )
        assert researching is not None
        report = create_fake_durable_research_report(
            researching,
            storage.list_signals(event_id),
        )
        assert storage.save_report_and_mark_reported(
            report,
            updated_at=PROCESS_TIME,
        )

    research_calls: list[int] = []
    notification_calls: list[int] = []
    with SQLiteStorage(database_path) as reopened:
        reopened.initialize()
        completed = EventManager(
            reopened,
            clock=FixedClock(PROCESS_TIME),
        ).process_pending(
            researcher=_recording_researcher(research_calls),
            notifier=_recording_notifier(notification_calls),
        )[0]

        assert completed.status is EventStatus.NOTIFIED
        assert research_calls == []
        assert notification_calls == [1]
        assert reopened.list_reports(event_id) == (report,)


def test_retryable_research_failure_restarts_research_after_reopen(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "research-failure-restart.sqlite3"
    event_id = _save_queued_event(database_path)
    initial_research_calls: list[int] = []

    def fail_research(
        event: Event,
        _: tuple[Signal, ...],
    ) -> ResearchReport:
        initial_research_calls.append(event.current_update)
        raise RuntimeError("private research detail")

    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        failed = EventManager(
            storage,
            clock=FixedClock(PROCESS_TIME),
        ).process_pending(
            researcher=fail_research,
            notifier=_recording_notifier([]),
        )[0]
        assert failed.status is EventStatus.FAILED

    retry_research_calls: list[int] = []
    notification_calls: list[int] = []
    with SQLiteStorage(database_path) as reopened:
        reopened.initialize()
        completed = EventManager(
            reopened,
            clock=FixedClock(PROCESS_TIME),
        ).process_pending(
            researcher=_recording_researcher(retry_research_calls),
            notifier=_recording_notifier(notification_calls),
        )[0]

        assert completed.status is EventStatus.NOTIFIED
        assert initial_research_calls == [1]
        assert retry_research_calls == [1]
        assert notification_calls == [1]
        failures = reopened.list_failures(event_id)
        assert len(failures) == 1
        assert failures[0].step is FailureStep.RESEARCH
        assert "private research detail" not in failures[0].description


def test_retryable_notification_failure_resumes_without_research(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "notification-failure-restart.sqlite3"
    event_id = _save_queued_event(database_path)
    research_calls: list[int] = []
    first_notification_calls: list[int] = []

    def fail_notification(event: Event, _: ResearchReport) -> None:
        first_notification_calls.append(event.current_update)
        raise RuntimeError("private notification detail")

    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        failed = EventManager(
            storage,
            clock=FixedClock(PROCESS_TIME),
        ).process_pending(
            researcher=_recording_researcher(research_calls),
            notifier=fail_notification,
        )[0]
        assert failed.status is EventStatus.FAILED

    retry_notification_calls: list[int] = []
    with SQLiteStorage(database_path) as reopened:
        reopened.initialize()
        completed = EventManager(
            reopened,
            clock=FixedClock(PROCESS_TIME),
        ).process_pending(
            researcher=_recording_researcher(research_calls),
            notifier=_recording_notifier(retry_notification_calls),
        )[0]

        assert completed.status is EventStatus.NOTIFIED
        assert research_calls == [1]
        assert first_notification_calls == [1]
        assert retry_notification_calls == [1]
        assert len(reopened.list_reports(event_id)) == 1
        attempts = reopened.list_notification_attempts(event_id)
        assert [attempt.succeeded for attempt in attempts] == [False, True]
        assert len(reopened.list_failures(event_id)) == 1
        assert reopened.list_failures(event_id)[0].step is FailureStep.NOTIFICATION


def _save_queued_event(database_path: Path) -> str:
    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        result = EventManager(
            storage,
            clock=FixedClock(PROCESS_TIME),
        ).handle_signal(MARKET_SIGNAL)
        assert result.event is not None
        return result.event.event_id


def _recording_researcher(
    calls: list[int],
) -> Callable[[Event, tuple[Signal, ...]], ResearchReport]:
    def research(
        event: Event,
        signals: tuple[Signal, ...],
    ) -> ResearchReport:
        calls.append(event.current_update)
        return create_fake_durable_research_report(event, signals)

    return research


def _recording_notifier(
    calls: list[int],
) -> Callable[[Event, ResearchReport], None]:
    def notify(event: Event, _: ResearchReport) -> None:
        calls.append(event.current_update)

    return notify
