"""Synchronous durable offline application flow."""

from dataclasses import dataclass
from pathlib import Path

from investment_assistant.clock import Clock
from investment_assistant.detection import detect_market_signal, detect_news_signal
from investment_assistant.event_manager import EventManager
from investment_assistant.fixture_readers import (
    load_market_fixture,
    load_news_fixture,
)
from investment_assistant.models import (
    Event,
    MarketRecord,
    MarketSignal,
    NewsRecord,
    NewsSignal,
    NotificationAttempt,
    ProcessingFailure,
    ResearchReport,
    Signal,
)
from investment_assistant.reporting import (
    Notifier,
    Researcher,
    create_fake_research_report,
    emit_console_notification,
)
from investment_assistant.storage import SQLiteStorage


@dataclass(frozen=True, slots=True)
class OfflineRunResult:
    """Observable durable state after one offline application run."""

    accepted_signal_ids: tuple[str, ...]
    events: tuple[Event, ...]
    processed_events: tuple[Event, ...]
    reports: tuple[ResearchReport, ...]
    notification_attempts: tuple[NotificationAttempt, ...]
    failures: tuple[ProcessingFailure, ...]


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Fixture normalization, detection, and durable processing results."""

    market_record: MarketRecord | None
    news_record: NewsRecord | None
    market_signal: MarketSignal | None
    news_signal: NewsSignal | None
    durable: OfflineRunResult


def run_offline_signals(
    *,
    database_path: Path,
    signals: tuple[Signal, ...],
    clock: Clock,
    researcher: Researcher = create_fake_research_report,
    notifier: Notifier = emit_console_notification,
) -> OfflineRunResult:
    """Submit qualifying signals and process the saved event worklist."""

    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        accepted_signal_ids: list[str] = []
        for signal in signals:
            if manager.handle_signal(signal).accepted:
                accepted_signal_ids.append(signal.signal_id)
        processed_events = manager.process_pending(
            researcher=researcher,
            notifier=notifier,
        )
        events = storage.list_events()
        reports = tuple(
            report
            for event in events
            for report in storage.list_reports(event.event_id)
        )
        notification_attempts = tuple(
            attempt
            for event in events
            for attempt in storage.list_notification_attempts(event.event_id)
        )
        failures = tuple(
            failure
            for event in events
            for failure in storage.list_failures(event.event_id)
        )
        return OfflineRunResult(
            accepted_signal_ids=tuple(accepted_signal_ids),
            events=events,
            processed_events=processed_events,
            reports=reports,
            notification_attempts=notification_attempts,
            failures=failures,
        )


def run_pipeline(
    *,
    database_path: Path,
    tracked_symbol: str,
    clock: Clock,
    market_fixture_path: Path | None = None,
    news_fixture_path: Path | None = None,
    researcher: Researcher = create_fake_research_report,
    notifier: Notifier = emit_console_notification,
) -> PipelineResult:
    """Run available offline fixtures through the shared durable flow."""

    market_record = (
        None
        if market_fixture_path is None
        else load_market_fixture(market_fixture_path)
    )
    news_record = (
        None if news_fixture_path is None else load_news_fixture(news_fixture_path)
    )
    market_signal = (
        None
        if market_record is None
        else detect_market_signal(market_record, tracked_symbol=tracked_symbol)
    )
    news_signal = (
        None
        if news_record is None
        else detect_news_signal(news_record, tracked_symbol=tracked_symbol)
    )
    signals: list[Signal] = []
    if market_signal is not None:
        signals.append(market_signal)
    if news_signal is not None:
        signals.append(news_signal)

    durable = run_offline_signals(
        database_path=database_path,
        signals=tuple(signals),
        clock=clock,
        researcher=researcher,
        notifier=notifier,
    )
    return PipelineResult(
        market_record=market_record,
        news_record=news_record,
        market_signal=market_signal,
        news_signal=news_signal,
        durable=durable,
    )
