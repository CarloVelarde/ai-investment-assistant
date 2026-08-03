"""Tests for durable research and notification processing."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
    NewsSignal,
    ResearchReport,
    Signal,
    SignalDirection,
    SignalImportance,
    SourceDetails,
)
from investment_assistant.reporting import create_fake_research_report
from investment_assistant.storage import SQLiteStorage

SIGNAL_TIME = datetime(2026, 2, 3, 15, 30, tzinfo=UTC)
PROCESS_TIME = datetime(2026, 2, 3, 16, 0, tzinfo=UTC)
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
    importance=SignalImportance.MODERATE,
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


def test_only_latest_waiting_update_is_researched_and_report_precedes_delivery(
    tmp_path: Path,
) -> None:
    higher = replace(
        MARKET_SIGNAL,
        signal_id="market-acme-down-2",
        importance=SignalImportance.HIGH,
    )
    new_window = replace(
        higher,
        signal_id="market-acme-down-3",
        window=MarketWindow.FIVE_DAYS,
    )
    research_calls: list[tuple[int, tuple[Signal, ...]]] = []
    notification_calls: list[int] = []
    with SQLiteStorage(tmp_path / "latest-processing.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(PROCESS_TIME))
        manager.handle_signal(MARKET_SIGNAL)
        manager.handle_signal(higher)
        manager.handle_signal(new_window)
        queued = manager.handle_signal(NEWS_SIGNAL)
        assert queued.event is not None

        def research(
            event: Event,
            signals: tuple[Signal, ...],
        ) -> ResearchReport:
            research_calls.append((event.current_update, signals))
            assert event.status is EventStatus.RESEARCHING
            saved_event = storage.get_event(event.event_id)
            assert saved_event is not None
            assert saved_event.status is EventStatus.RESEARCHING
            return create_fake_research_report(event, signals)

        def notify(event: Event, report: ResearchReport) -> None:
            notification_calls.append(event.current_update)
            assert (
                storage.get_report_for_update(
                    event.event_id,
                    event.current_update,
                )
                == report
            )
            saved_event = storage.get_event(event.event_id)
            assert saved_event is not None
            assert saved_event.status is EventStatus.REPORTED

        results = manager.process_pending(researcher=research, notifier=notify)

        assert [update for update, _ in research_calls] == [4]
        assert len(research_calls[0][1]) == 4
        assert notification_calls == [4]
        assert len(results) == 1
        assert results[0].status is EventStatus.NOTIFIED
        assert [
            report.event_update
            for report in storage.list_reports(queued.event.event_id)
        ] == [4]
        attempts = storage.list_notification_attempts(queued.event.event_id)
        assert len(attempts) == 1
        assert attempts[0].succeeded is True


def test_report_for_outdated_update_is_discarded_and_never_notified(
    tmp_path: Path,
) -> None:
    important_update = replace(
        MARKET_SIGNAL,
        signal_id="market-acme-down-2",
        importance=SignalImportance.HIGH,
    )
    research_updates: list[int] = []
    notification_updates: list[int] = []
    with SQLiteStorage(tmp_path / "outdated-report.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(PROCESS_TIME))
        initial = manager.handle_signal(MARKET_SIGNAL)
        assert initial.event is not None

        def research_then_update(
            event: Event,
            signals: tuple[Signal, ...],
        ) -> ResearchReport:
            research_updates.append(event.current_update)
            manager.handle_signal(important_update)
            return create_fake_research_report(event, signals)

        def notify(event: Event, _: ResearchReport) -> None:
            notification_updates.append(event.current_update)

        result = manager.process_event(
            initial.event.event_id,
            researcher=research_then_update,
            notifier=notify,
        )

        assert result is not None
        assert result.current_update == 2
        assert result.status is EventStatus.QUEUED
        assert research_updates == [1]
        assert notification_updates == []
        assert storage.list_reports(result.event_id) == ()
        assert storage.list_notification_attempts(result.event_id) == ()

        completed = manager.process_event(
            result.event_id,
            researcher=create_fake_research_report,
            notifier=notify,
        )

        assert completed is not None
        assert completed.status is EventStatus.NOTIFIED
        assert notification_updates == [2]


def test_failed_notification_retries_without_research_and_completion_is_idempotent(
    tmp_path: Path,
) -> None:
    research_calls: list[int] = []
    notification_calls: list[int] = []
    should_fail = True

    def research(
        event: Event,
        signals: tuple[Signal, ...],
    ) -> ResearchReport:
        research_calls.append(event.current_update)
        return create_fake_research_report(event, signals)

    def notify(event: Event, _: ResearchReport) -> None:
        nonlocal should_fail
        notification_calls.append(event.current_update)
        if should_fail:
            should_fail = False
            raise RuntimeError("private delivery detail")

    with SQLiteStorage(tmp_path / "notification-retry.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(PROCESS_TIME))
        queued = manager.handle_signal(MARKET_SIGNAL)
        assert queued.event is not None

        failed = manager.process_pending(researcher=research, notifier=notify)[0]

        assert failed.status is EventStatus.FAILED
        assert research_calls == [1]
        assert notification_calls == [1]
        attempts = storage.list_notification_attempts(failed.event_id)
        failures = storage.list_failures(failed.event_id)
        assert len(attempts) == 1
        assert attempts[0].succeeded is False
        assert "private delivery detail" not in (attempts[0].safe_error or "")
        assert len(failures) == 1
        assert failures[0].step is FailureStep.NOTIFICATION
        assert failures[0].retryable is True

        completed = manager.process_pending(researcher=research, notifier=notify)[0]

        assert completed.status is EventStatus.NOTIFIED
        assert completed.last_notified_at == PROCESS_TIME
        assert research_calls == [1]
        assert notification_calls == [1, 1]
        attempts = storage.list_notification_attempts(completed.event_id)
        assert [attempt.succeeded for attempt in attempts] == [False, True]

        assert manager.process_pending(researcher=research, notifier=notify) == ()
        assert research_calls == [1]
        assert notification_calls == [1, 1]
        assert len(storage.list_reports(completed.event_id)) == 1


def test_later_important_update_is_immediately_eligible_after_notification(
    tmp_path: Path,
) -> None:
    research_calls: list[int] = []
    notification_calls: list[int] = []

    def research(
        event: Event,
        signals: tuple[Signal, ...],
    ) -> ResearchReport:
        research_calls.append(event.current_update)
        return create_fake_research_report(event, signals)

    def notify(event: Event, _: ResearchReport) -> None:
        notification_calls.append(event.current_update)

    with SQLiteStorage(tmp_path / "later-update.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(PROCESS_TIME))
        first = manager.handle_signal(MARKET_SIGNAL)
        assert first.event is not None
        manager.process_pending(researcher=research, notifier=notify)
        later_signal = replace(
            MARKET_SIGNAL,
            signal_id="market-acme-down-2",
            occurred_at=SIGNAL_TIME + timedelta(days=5),
            window=MarketWindow.FIVE_DAYS,
        )

        later = manager.handle_signal(later_signal)

        assert later.event is not None
        assert later.event.current_update == 2
        assert later.event.status is EventStatus.QUEUED
        completed = manager.process_pending(researcher=research, notifier=notify)[0]
        assert completed.status is EventStatus.NOTIFIED
        assert research_calls == [1, 2]
        assert notification_calls == [1, 2]
        assert [
            report.event_update for report in storage.list_reports(completed.event_id)
        ] == [1, 2]
