"""Deterministic grouping and initial promotion of qualifying signals."""

from dataclasses import dataclass, replace

from investment_assistant.clock import Clock
from investment_assistant.models import (
    Event,
    EventStatus,
    MarketSignal,
    MarketWindow,
    Signal,
    SignalDirection,
    SignalImportance,
)
from investment_assistant.storage import SQLiteStorage


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
        if isinstance(signal, MarketSignal) and signal.window not in market_windows:
            market_windows = (*market_windows, signal.window)
        return replace(
            event,
            importance=_higher_importance(event.importance, signal.importance),
            market_windows=market_windows,
            updated_at=self._clock.now(),
        )


def _higher_importance(
    left: SignalImportance,
    right: SignalImportance,
) -> SignalImportance:
    return left if left.rank >= right.rank else right
