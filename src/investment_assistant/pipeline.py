"""Synchronous durable offline application flow."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from investment_assistant.clock import Clock
from investment_assistant.detection import detect_market_signal, detect_news_signal
from investment_assistant.event_manager import EventManager
from investment_assistant.fixture_readers import (
    load_market_fixture,
    load_market_history_fixture,
    load_news_fixture,
)
from investment_assistant.market_detection import (
    detect_daily_from_storage,
    detect_fast_from_storage,
    maintain_market_episodes,
)
from investment_assistant.models import (
    Event,
    MarketBar,
    MarketRecord,
    MarketSignal,
    MarketTimeframe,
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
class MarketHistoryRunResult:
    """Observable state after one bar-history offline run."""

    scenario: str
    watchlist: tuple[str, ...]
    accepted_signal_ids: tuple[str, ...]
    events: tuple[Event, ...]
    processed_events: tuple[Event, ...]
    reports: tuple[ResearchReport, ...]
    notification_attempts: tuple[NotificationAttempt, ...]
    failures: tuple[ProcessingFailure, ...]
    diagnostics: tuple[str, ...]
    closed_event_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MarketBarProcessingResult:
    """Committed outcome of ingesting and evaluating one market bar."""

    accepted_signal_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    closed_event_ids: tuple[str, ...] = ()


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


def run_market_history(
    *,
    database_path: Path,
    fixture_path: Path,
    clock: Clock,
    researcher: Researcher = create_fake_research_report,
    notifier: Notifier = emit_console_notification,
) -> MarketHistoryRunResult:
    """Ingest fixture bars, detect, group, close recovered episodes, then process."""

    scenario, watchlist, bars = load_market_history_fixture(fixture_path)
    watched = frozenset(watchlist)
    diagnostics: list[str] = []
    closed_event_ids: list[str] = []
    accepted_signal_ids: list[str] = []

    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        for bar in _bars_in_evaluation_order(bars):
            _sync_clock(clock, bar.end_at)
            outcome = process_market_bar(
                storage=storage,
                manager=manager,
                bar=bar,
                watchlist=watched,
                now=clock.now(),
            )
            diagnostics.extend(outcome.diagnostics)
            accepted_signal_ids.extend(outcome.accepted_signal_ids)
            closed_event_ids.extend(outcome.closed_event_ids)

        processed_events = manager.process_pending(
            researcher=researcher,
            notifier=notifier,
        )
        events = storage.list_events()
        return MarketHistoryRunResult(
            scenario=scenario,
            watchlist=watchlist,
            accepted_signal_ids=tuple(accepted_signal_ids),
            events=events,
            processed_events=processed_events,
            reports=tuple(
                report
                for event in events
                for report in storage.list_reports(event.event_id)
            ),
            notification_attempts=tuple(
                attempt
                for event in events
                for attempt in storage.list_notification_attempts(event.event_id)
            ),
            failures=tuple(
                failure
                for event in events
                for failure in storage.list_failures(event.event_id)
            ),
            diagnostics=tuple(diagnostics),
            closed_event_ids=tuple(closed_event_ids),
        )


def process_market_bar(
    *,
    storage: SQLiteStorage,
    manager: EventManager,
    bar: MarketBar,
    watchlist: frozenset[str],
    now: datetime,
    emit_signals: bool = True,
) -> MarketBarProcessingResult:
    """Atomically save one bar and commit every resulting state transition.

    When ``emit_signals`` is false, detector state still updates but signals
    are not sent to the event manager and episodes are not closed. Used for
    quiet replay of old backfill.
    """

    diagnostics: list[str] = []
    accepted_signal_ids: list[str] = []
    closed_event_ids: list[str] = []
    with storage.transaction():
        storage.save_market_bar(bar)
        if not bar.is_complete:
            return MarketBarProcessingResult()
        evaluation_tickers: tuple[str, ...]
        if bar.ticker in watchlist:
            evaluation_tickers = (bar.ticker,)
        elif bar.ticker == "SPY" and bar.timeframe is MarketTimeframe.ONE_DAY:
            evaluation_tickers = tuple(sorted(watchlist))
        else:
            return MarketBarProcessingResult()
        evaluation_through = (
            bar.end_at
            if bar.ticker == "SPY" and bar.timeframe is MarketTimeframe.ONE_DAY
            else bar.start_at
        )
        for ticker in evaluation_tickers:
            if bar.timeframe is MarketTimeframe.ONE_MINUTE:
                result = detect_fast_from_storage(
                    storage,
                    ticker,
                    now=now,
                    through_start_at=evaluation_through,
                )
            elif bar.timeframe is MarketTimeframe.ONE_DAY:
                result = detect_daily_from_storage(
                    storage,
                    ticker,
                    now=now,
                    through_start_at=evaluation_through,
                )
            else:
                continue
            diagnostics.extend(result.diagnostics)
            if emit_signals:
                for signal in result.signals:
                    if manager.handle_signal(signal).accepted:
                        accepted_signal_ids.append(signal.signal_id)
            if emit_signals and bar.timeframe is MarketTimeframe.ONE_DAY:
                closed = maintain_market_episodes(storage, ticker, now=now)
                closed_event_ids.extend(event.event_id for event in closed)
    return MarketBarProcessingResult(
        accepted_signal_ids=tuple(accepted_signal_ids),
        diagnostics=tuple(diagnostics),
        closed_event_ids=tuple(closed_event_ids),
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


def _bars_in_evaluation_order(bars: tuple[MarketBar, ...]) -> tuple[MarketBar, ...]:
    return tuple(
        sorted(
            bars,
            key=lambda bar: (
                bar.end_at,
                bar.start_at,
                0 if bar.ticker == "SPY" else 1,
                bar.ticker,
                bar.bar_id,
            ),
        )
    )


def _sync_clock(clock: Clock, when: datetime) -> None:
    advance_to = getattr(clock, "advance_to", None)
    if callable(advance_to):
        advance_to(when)
