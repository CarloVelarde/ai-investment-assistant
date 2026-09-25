"""Deterministic grouping, promotion, and processing of qualifying signals."""

import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

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
from investment_assistant.ops_log import watch
from investment_assistant.reporting import emit_console_notification
from investment_assistant.research import ResearchDeferred
from investment_assistant.storage import SQLiteStorage

if TYPE_CHECKING:
    from investment_assistant.delivery import DeliveryManager

type DurableResearcher = Callable[[Event, tuple[Signal, ...]], ResearchReport]
type DurableNotifier = Callable[[Event, ResearchReport], None]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SignalHandlingResult:
    """Observable result of submitting one signal to the event manager."""

    accepted: bool
    event: Event | None


class EventManager:
    """Own signal grouping and research eligibility decisions."""

    def __init__(
        self,
        storage: SQLiteStorage,
        *,
        clock: Clock,
        delivery_manager: DeliveryManager | None = None,
        log_console_fallback: bool = False,
    ) -> None:
        self._storage = storage
        self._clock = clock
        self._delivery_manager = delivery_manager
        self.last_delivery_attempted = False
        self._log_console_fallback = log_console_fallback
        self._console_fallback_logged = False

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
        max_research_runs: int | None = None,
    ) -> tuple[Event, ...]:
        """Process saved reports, then pending research from durable state.

        Offline callers omit ``max_research_runs`` and drain each event once.
        Live mode attempts one due delivery first, then starts at most one
        fair research run.
        """

        results: list[Event] = []
        self.last_delivery_attempted = False
        if self._delivery_manager is not None:
            self.last_delivery_attempted = self._delivery_manager.process_one()
        for event in self._storage.list_events():
            if event.status is EventStatus.NOTIFIED:
                continue
            if self._notification_ready(event):
                result = (
                    None
                    if self._delivery_manager is not None
                    else self._notify(event, notifier)
                )
                if result is not None:
                    results.append(result)
                continue
            if max_research_runs is None:
                result = self.process_event(
                    event.event_id,
                    researcher=researcher,
                    notifier=notifier,
                )
                if result is not None:
                    results.append(result)
        if max_research_runs is None:
            return tuple(results)
        for _ in range(max_research_runs):
            candidate = self._storage.next_research_event(now=self._clock.now())
            if candidate is None:
                break
            result = self._research(candidate, researcher, notifier)
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
        if self._notification_ready(event):
            if self._delivery_manager is not None:
                self.last_delivery_attempted = self._delivery_manager.process_one()
                return self._storage.get_event(event_id)
            return self._notify(event, notifier)
        if event.status is EventStatus.FAILED:
            failure = self._storage.get_latest_failure(
                event.event_id,
                event.current_update,
            )
            if failure is None or not failure.retryable:
                return event
        return self._research(event, researcher, notifier)

    def _notification_ready(self, event: Event) -> bool:
        if event.status is EventStatus.REPORTED:
            return True
        if event.status is not EventStatus.FAILED:
            return False
        failure = self._storage.get_latest_failure(
            event.event_id,
            event.current_update,
        )
        return (
            failure is not None
            and failure.retryable
            and failure.step is FailureStep.NOTIFICATION
        )

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
        previous_deferral = self._storage.get_research_deferral(
            started.event_id, started.current_update
        )
        try:
            report = researcher(started, signals)
            _validate_report(report, started)
        except ResearchDeferred:
            deferral = self._storage.get_research_deferral(
                started.event_id, started.current_update
            )
            if deferral is not None and deferral != previous_deferral:
                fields = {
                    "ticker": started.ticker,
                    "event_id": started.event_id,
                    "event_update": started.current_update,
                    "reason": deferral.reason,
                }
                logger.info("Research deferred", extra=fields)
                watch("Research deferred", **fields)
            return self._storage.get_event(started.event_id)
        except Exception as error:
            failure = self._new_failure(started, FailureStep.RESEARCH, error)
            self._storage.save_failure_and_mark_failed(
                failure,
                updated_at=self._clock.now(),
            )
            # Ignore a refused failure save the same way as a refused report
            # save: reload durable truth and stop this pass.
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
        if self._delivery_manager is not None:
            return reported
        return self._notify(reported, notifier)

    def _notify(
        self,
        event: Event,
        notifier: DurableNotifier,
    ) -> Event | None:
        if self._storage.has_external_delivery(event.event_id, event.current_update):
            return None
        # Re-check the saved event before any user-facing side effect so an
        # outdated report is not sent after a newer important update.
        deliverable = self._deliverable_notification(event)
        if deliverable is None:
            return self._storage.get_event(event.event_id)
        current, report = deliverable

        attempted_at = self._clock.now()
        attempt_number = (
            len(self._storage.list_notification_attempts(current.event_id)) + 1
        )
        attempt_id = (
            f"attempt:{current.event_id}:{current.current_update}:{attempt_number}"
        )
        if notifier is emit_console_notification:
            if self._log_console_fallback and not self._console_fallback_logged:
                with suppress(Exception):
                    logger.info(
                        "Discord not configured; using console for saved reports"
                    )
                watch("Discord not configured; using console for saved reports")
                self._console_fallback_logged = True
            attempt = NotificationAttempt(
                attempt_id=attempt_id,
                event_id=current.event_id,
                event_update=current.current_update,
                attempted_at=attempted_at,
                succeeded=True,
            )
            if self._storage.save_notification_result(
                attempt, failure=None, updated_at=self._clock.now()
            ):
                with suppress(Exception):
                    notifier(current, report)
            return self._storage.get_event(current.event_id)
        try:
            notifier(current, report)
        except Exception as error:
            description = _safe_failure_description(
                FailureStep.NOTIFICATION,
                error,
            )
            attempt = NotificationAttempt(
                attempt_id=attempt_id,
                event_id=current.event_id,
                event_update=current.current_update,
                attempted_at=attempted_at,
                succeeded=False,
                safe_error=description,
            )
            failure = self._new_failure(
                current,
                FailureStep.NOTIFICATION,
                error,
            )
            if not self._storage.save_notification_result(
                attempt,
                failure=failure,
                updated_at=self._clock.now(),
            ):
                # Database refused (event moved on or no longer deliverable).
                return self._storage.get_event(current.event_id)
        else:
            attempt = NotificationAttempt(
                attempt_id=attempt_id,
                event_id=current.event_id,
                event_update=current.current_update,
                attempted_at=attempted_at,
                succeeded=True,
            )
            if not self._storage.save_notification_result(
                attempt,
                failure=None,
                updated_at=self._clock.now(),
            ):
                # Database refused (event moved on or no longer deliverable).
                return self._storage.get_event(current.event_id)
        return self._storage.get_event(current.event_id)

    def _deliverable_notification(
        self,
        event: Event,
    ) -> tuple[Event, ResearchReport] | None:
        """Return the event and report only if this update is still ready to notify."""

        current = self._storage.get_event(event.event_id)
        if current is None:
            return None
        if current.current_update != event.current_update:
            return None
        if current.status not in {EventStatus.REPORTED, EventStatus.FAILED}:
            return None
        report = self._storage.get_report_for_update(
            current.event_id,
            current.current_update,
        )
        if report is None or report.event_update != current.current_update:
            return None
        return current, report

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
