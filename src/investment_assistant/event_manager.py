"""Deterministic grouping, promotion, and processing of qualifying signals."""

from collections.abc import Callable
from dataclasses import dataclass, replace

from investment_assistant.clock import Clock
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
    Signal,
    SignalDirection,
    SignalImportance,
)
from investment_assistant.storage import SQLiteStorage

type DurableResearcher = Callable[[Event, tuple[Signal, ...]], ResearchReport]
type DurableNotifier = Callable[[Event, ResearchReport], None]


@dataclass(frozen=True, slots=True)
class SignalHandlingResult:
    """Observable result of submitting one signal to the event manager."""

    accepted: bool
    event: Event | None


class EventManager:
    """Own signal grouping and research eligibility decisions."""

    def __init__(self, storage: SQLiteStorage, *, clock: Clock) -> None:
        self._storage = storage
        self._clock = clock

    def handle_signal(self, signal: Signal) -> SignalHandlingResult:
        """Accept a signal once and group it into one durable event."""

        if self._storage.has_signal(signal.signal_id):
            return SignalHandlingResult(accepted=False, event=None)

        existing_event = self._find_related_event(signal)
        if existing_event is None:
            event = self._new_event(signal)
        else:
            event = self._enrich_event(existing_event, signal)

        if not self._storage.record_signal(signal, event):
            return SignalHandlingResult(accepted=False, event=None)
        return SignalHandlingResult(accepted=True, event=event)

    def process_pending(
        self,
        *,
        researcher: DurableResearcher,
        notifier: DurableNotifier,
    ) -> tuple[Event, ...]:
        """Process each saved event from its current durable stage."""

        results: list[Event] = []
        for event in self._storage.list_events():
            if event.status is EventStatus.NOTIFIED:
                continue
            result = self.process_event(
                event.event_id,
                researcher=researcher,
                notifier=notifier,
            )
            if result is not None:
                results.append(result)
        return tuple(results)

    def process_event(
        self,
        event_id: str,
        *,
        researcher: DurableResearcher,
        notifier: DurableNotifier,
    ) -> Event | None:
        """Resume one event from its current durable stage."""

        event = self._storage.get_event(event_id)
        if event is None or event.status is EventStatus.NOTIFIED:
            return event
        if event.status is EventStatus.REPORTED:
            return self._notify(event, notifier)
        if event.status is EventStatus.FAILED:
            failure = self._storage.get_latest_failure(
                event.event_id,
                event.current_update,
            )
            if failure is None or not failure.retryable:
                return event
            if failure.step is FailureStep.NOTIFICATION:
                return self._notify(event, notifier)
        return self._research(event, researcher, notifier)

    def _find_related_event(self, signal: Signal) -> Event | None:
        if isinstance(signal, MarketSignal):
            directional_events = self._storage.find_direction_events(
                signal.ticker,
                signal.direction,
            )
            return directional_events[0] if directional_events else None

        if signal.direction is not None:
            market_events = self._storage.find_direction_events(
                signal.ticker,
                signal.direction,
                with_market_signal=True,
            )
            if len(market_events) == 1:
                return market_events[0]

        category_events = self._storage.find_category_events(
            signal.ticker,
            signal.category,
        )
        return category_events[0] if category_events else None

    def _new_event(self, signal: Signal) -> Event:
        now = self._clock.now()
        direction: SignalDirection | None
        category: str | None
        market_windows: tuple[MarketWindow, ...]
        if isinstance(signal, MarketSignal):
            direction = signal.direction
            category = None
            market_windows = (signal.window,)
        else:
            direction = signal.direction
            category = signal.category
            market_windows = ()
        return Event(
            event_id=f"event:{signal.signal_id}",
            ticker=signal.ticker,
            direction=direction,
            category=category,
            importance=signal.importance,
            market_windows=market_windows,
            current_update=1,
            status=EventStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )

    def _enrich_event(self, event: Event, signal: Signal) -> Event:
        market_windows = event.market_windows
        new_market_window = (
            isinstance(signal, MarketSignal) and signal.window not in market_windows
        )
        if isinstance(signal, MarketSignal) and new_market_window:
            market_windows = (*market_windows, signal.window)
        important_update = (
            signal.importance.rank > event.importance.rank
            or new_market_window
            or isinstance(signal, NewsSignal)
        )
        return replace(
            event,
            importance=_higher_importance(event.importance, signal.importance),
            market_windows=market_windows,
            current_update=(
                event.current_update + 1 if important_update else event.current_update
            ),
            status=EventStatus.QUEUED if important_update else event.status,
            updated_at=self._clock.now(),
        )

    def _research(
        self,
        event: Event,
        researcher: DurableResearcher,
        notifier: DurableNotifier,
    ) -> Event | None:
        started = self._storage.mark_researching(
            event.event_id,
            event.current_update,
            updated_at=self._clock.now(),
        )
        if started is None:
            return self._storage.get_event(event.event_id)
        signals = self._storage.list_signals(started.event_id)
        try:
            report = researcher(started, signals)
            _validate_report(report, started)
        except Exception as error:
            failure = self._new_failure(started, FailureStep.RESEARCH, error)
            self._storage.save_failure_and_mark_failed(
                failure,
                updated_at=self._clock.now(),
            )
            return self._storage.get_event(started.event_id)

        saved = self._storage.save_report_and_mark_reported(
            report,
            updated_at=self._clock.now(),
        )
        if not saved:
            return self._storage.get_event(started.event_id)
        reported = self._storage.get_event(started.event_id)
        if reported is None:
            return None
        return self._notify(reported, notifier)

    def _notify(
        self,
        event: Event,
        notifier: DurableNotifier,
    ) -> Event | None:
        report = self._storage.get_report_for_update(
            event.event_id,
            event.current_update,
        )
        if report is None:
            return event

        attempted_at = self._clock.now()
        attempt_number = (
            len(self._storage.list_notification_attempts(event.event_id)) + 1
        )
        attempt_id = f"attempt:{event.event_id}:{event.current_update}:{attempt_number}"
        try:
            notifier(event, report)
        except Exception as error:
            description = _safe_failure_description(
                FailureStep.NOTIFICATION,
                error,
            )
            attempt = NotificationAttempt(
                attempt_id=attempt_id,
                event_id=event.event_id,
                event_update=event.current_update,
                attempted_at=attempted_at,
                succeeded=False,
                safe_error=description,
            )
            failure = self._new_failure(
                event,
                FailureStep.NOTIFICATION,
                error,
            )
            self._storage.save_notification_result(
                attempt,
                failure=failure,
                updated_at=self._clock.now(),
            )
        else:
            attempt = NotificationAttempt(
                attempt_id=attempt_id,
                event_id=event.event_id,
                event_update=event.current_update,
                attempted_at=attempted_at,
                succeeded=True,
            )
            self._storage.save_notification_result(
                attempt,
                failure=None,
                updated_at=self._clock.now(),
            )
        return self._storage.get_event(event.event_id)

    def _new_failure(
        self,
        event: Event,
        step: FailureStep,
        error: Exception,
    ) -> ProcessingFailure:
        failure_number = len(self._storage.list_failures(event.event_id)) + 1
        return ProcessingFailure(
            failure_id=(
                f"failure:{event.event_id}:{event.current_update}:{failure_number}"
            ),
            event_id=event.event_id,
            event_update=event.current_update,
            step=step,
            retryable=True,
            occurred_at=self._clock.now(),
            description=_safe_failure_description(step, error),
        )


def _higher_importance(
    left: SignalImportance,
    right: SignalImportance,
) -> SignalImportance:
    return left if left.rank >= right.rank else right


def _validate_report(report: ResearchReport, event: Event) -> None:
    if (
        report.event_id != event.event_id
        or report.event_update != event.current_update
        or report.ticker != event.ticker
    ):
        raise ValueError("research report does not match the current event update")


def _safe_failure_description(step: FailureStep, error: Exception) -> str:
    return f"{step.value.lower()} failed: {type(error).__name__}"
