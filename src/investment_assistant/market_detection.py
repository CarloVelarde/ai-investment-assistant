"""Deterministic fast and daily market detectors.

Detectors emit ``MarketSignal`` values and update detector state. They do
not create events or request research.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from investment_assistant.detection import stable_signal_id
from investment_assistant.market_metrics import (
    DRAWDOWN_LOOKBACK_BARS,
    FAST_LOOKBACK_BARS,
    FAST_VOLUME_LOOKBACK,
    MULTI_DAY_LOOKBACKS,
    RULE_ABRUPT_MOVE,
    RULE_DRAWDOWN_FROM_HIGH,
    RULE_MULTI_DAY_MOVE,
    RULE_RELATIVE_TO_SPY,
    UNUSED_VOLUME_RATIO,
    dampen_importance,
    directional_magnitude,
    drawdown_from_high,
    rally_from_low,
    relative_return,
    signed_return,
    thresholds_for,
)
from investment_assistant.models import (
    DetectorState,
    Event,
    MarketBar,
    MarketSignal,
    MarketTimeframe,
    MarketWindow,
    SignalDirection,
    SignalImportance,
    SourceDetails,
)
from investment_assistant.storage import SQLiteStorage

SPY_TICKER = "SPY"
DAILY_RULE_WINDOWS: tuple[tuple[str, MarketWindow], ...] = (
    (RULE_MULTI_DAY_MOVE, MarketWindow.FIVE_DAYS),
    (RULE_MULTI_DAY_MOVE, MarketWindow.TWENTY_DAYS),
    (RULE_DRAWDOWN_FROM_HIGH, MarketWindow.TWENTY_DAYS),
    (RULE_RELATIVE_TO_SPY, MarketWindow.FIVE_DAYS),
    (RULE_RELATIVE_TO_SPY, MarketWindow.TWENTY_DAYS),
)

type DailyStateKey = tuple[str, MarketWindow, SignalDirection]


@dataclass(frozen=True, slots=True)
class CrossingDecision:
    """Emit-or-quiet result for one detector key."""

    emit_importance: SignalImportance | None
    next_state: DetectorState


@dataclass(frozen=True, slots=True)
class MarketDetectionResult:
    """Signals, updated state, and any skipped-rule notes from one scan."""

    signals: tuple[MarketSignal, ...]
    states: tuple[DetectorState, ...]
    diagnostics: tuple[str, ...] = ()


FAST_HISTORY_LIMIT = FAST_LOOKBACK_BARS + 1
DAILY_HISTORY_LIMIT = max(MULTI_DAY_LOOKBACKS.values()) + 1


def decide_crossing(
    *,
    ticker: str,
    rule: str,
    window: MarketWindow,
    direction: SignalDirection,
    magnitude: Decimal,
    candidate_importance: SignalImportance | None,
    previous: DetectorState | None,
    now: datetime,
    evaluated_at: datetime | None = None,
) -> CrossingDecision:
    """Apply cross / escalate / quiet / rearm rules for one detector key."""

    if magnitude < 0:
        raise ValueError("magnitude must not be negative")
    table = thresholds_for(rule, window)
    last = None if previous is None else previous.last_emitted_importance
    state_updated_at = now if previous is None else max(now, previous.updated_at)
    state_evaluated_at = evaluated_at
    if state_evaluated_at is None and previous is not None:
        state_evaluated_at = previous.last_evaluated_at

    if candidate_importance is None and magnitude < table.rearm_line:
        return CrossingDecision(
            None,
            _state(
                ticker,
                rule,
                window,
                direction,
                last_emitted_importance=None,
                now=state_updated_at,
                evaluated_at=state_evaluated_at,
            ),
        )

    if candidate_importance is not None and (
        last is None or candidate_importance.rank > last.rank
    ):
        return CrossingDecision(
            candidate_importance,
            _state(
                ticker,
                rule,
                window,
                direction,
                last_emitted_importance=candidate_importance,
                now=state_updated_at,
                evaluated_at=state_evaluated_at,
            ),
        )

    return CrossingDecision(
        None,
        _state(
            ticker,
            rule,
            window,
            direction,
            last_emitted_importance=last,
            now=state_updated_at,
            evaluated_at=state_evaluated_at,
        ),
    )


def evaluate_and_store_crossing(
    storage: SQLiteStorage,
    *,
    ticker: str,
    rule: str,
    window: MarketWindow,
    direction: SignalDirection,
    magnitude: Decimal,
    now: datetime,
) -> CrossingDecision:
    """Load state, apply the crossing rules, and persist the next state."""

    importance = thresholds_for(rule, window).importance_for(magnitude)
    previous = storage.get_detector_state(ticker, rule, window, direction)
    decision = decide_crossing(
        ticker=ticker,
        rule=rule,
        window=window,
        direction=direction,
        magnitude=magnitude,
        candidate_importance=importance,
        previous=previous,
        now=now,
        evaluated_at=now,
    )
    storage.save_detector_state(decision.next_state)
    return decision


def detect_fast_signals(
    bars: Sequence[MarketBar],
    previous_states: Mapping[SignalDirection, DetectorState | None],
    *,
    now: datetime,
) -> MarketDetectionResult:
    """Evaluate abrupt one-hour movement on completed minute bars."""

    completed = _usable_bars(bars, MarketTimeframe.ONE_MINUTE)
    if len(completed) < FAST_LOOKBACK_BARS + 1:
        return MarketDetectionResult((), ())

    latest = completed[-1]
    baseline = completed[-(FAST_LOOKBACK_BARS + 1)]
    move = signed_return(baseline.close, latest.close)
    stored_volume, volume_for_dampen = _fast_volume(completed)
    table = thresholds_for(RULE_ABRUPT_MOVE, MarketWindow.ONE_HOUR)

    signals: list[MarketSignal] = []
    states: list[DetectorState] = []
    for direction in SignalDirection:
        previous = previous_states.get(direction)
        if _key_already_evaluated(previous, latest.start_at):
            continue
        magnitude = directional_magnitude(move, direction)
        candidate = dampen_importance(
            table.importance_for(magnitude),
            volume_for_dampen,
        )
        decision = decide_crossing(
            ticker=latest.ticker,
            rule=RULE_ABRUPT_MOVE,
            window=MarketWindow.ONE_HOUR,
            direction=direction,
            magnitude=magnitude,
            candidate_importance=candidate,
            previous=previous,
            now=now,
            evaluated_at=latest.start_at,
        )
        states.append(decision.next_state)
        if decision.emit_importance is not None:
            signals.append(
                _market_signal(
                    latest,
                    importance=decision.emit_importance,
                    direction=direction,
                    rule=RULE_ABRUPT_MOVE,
                    window=MarketWindow.ONE_HOUR,
                    magnitude=magnitude,
                    volume_ratio=stored_volume,
                    baseline_price=baseline.close,
                    comparison_return_ratio=None,
                )
            )
    return MarketDetectionResult(tuple(signals), tuple(states))


def detect_daily_signals(
    ticker_bars: Sequence[MarketBar],
    spy_bars: Sequence[MarketBar],
    previous_states: Mapping[DailyStateKey, DetectorState | None],
    *,
    now: datetime,
) -> MarketDetectionResult:
    """Evaluate the after-close daily rules on completed daily bars."""

    ticker_days = _usable_bars(ticker_bars, MarketTimeframe.ONE_DAY)
    if not ticker_days:
        return MarketDetectionResult((), ())
    spy_days = _usable_bars(spy_bars, MarketTimeframe.ONE_DAY)
    latest = ticker_days[-1]

    signals: list[MarketSignal] = []
    states: list[DetectorState] = []
    diagnostics: list[str] = []

    for window, lookback in MULTI_DAY_LOOKBACKS.items():
        if len(ticker_days) < lookback + 1:
            continue
        start = ticker_days[-(lookback + 1)]
        move = signed_return(start.close, latest.close)
        _collect_signed_rule(
            latest,
            rule=RULE_MULTI_DAY_MOVE,
            window=window,
            signed_value=move,
            previous_states=previous_states,
            now=now,
            signals=signals,
            states=states,
            baseline_price=start.close,
            comparison_return_ratio=None,
        )

    if len(ticker_days) >= DRAWDOWN_LOOKBACK_BARS:
        window_bars = ticker_days[-DRAWDOWN_LOOKBACK_BARS:]
        magnitudes = {
            SignalDirection.DOWN: drawdown_from_high(
                max(bar.high for bar in window_bars),
                latest.close,
            ),
            SignalDirection.UP: rally_from_low(
                min(bar.low for bar in window_bars),
                latest.close,
            ),
        }
        for direction, magnitude in magnitudes.items():
            _collect_one_key(
                latest,
                rule=RULE_DRAWDOWN_FROM_HIGH,
                window=MarketWindow.TWENTY_DAYS,
                direction=direction,
                magnitude=magnitude,
                previous_states=previous_states,
                now=now,
                signals=signals,
                states=states,
                baseline_price=(
                    max(bar.high for bar in window_bars)
                    if direction is SignalDirection.DOWN
                    else min(bar.low for bar in window_bars)
                ),
                comparison_return_ratio=None,
            )

    for window, lookback in MULTI_DAY_LOOKBACKS.items():
        if len(ticker_days) < lookback + 1:
            continue
        start = ticker_days[-(lookback + 1)]
        spy_latest = _bar_on_date(spy_days, latest.start_at.date())
        spy_start = _bar_on_date(spy_days, start.start_at.date())
        if spy_latest is None or spy_start is None:
            diagnostics.append(
                "relative_to_spy skipped for "
                f"{latest.ticker} {window.value}: missing SPY history"
            )
            continue
        relative = relative_return(
            signed_return(start.close, latest.close),
            signed_return(spy_start.close, spy_latest.close),
        )
        _collect_signed_rule(
            latest,
            rule=RULE_RELATIVE_TO_SPY,
            window=window,
            signed_value=relative,
            previous_states=previous_states,
            now=now,
            signals=signals,
            states=states,
            baseline_price=start.close,
            comparison_return_ratio=signed_return(spy_start.close, spy_latest.close),
        )

    return MarketDetectionResult(tuple(signals), tuple(states), tuple(diagnostics))


def detect_fast_from_storage(
    storage: SQLiteStorage,
    ticker: str,
    *,
    now: datetime,
    through_start_at: datetime | None = None,
) -> MarketDetectionResult:
    """Run the fast detector from persisted minute bars and state."""

    ticker = ticker.strip().upper()
    bars = storage.list_market_bars(
        ticker,
        MarketTimeframe.ONE_MINUTE,
        complete_only=True,
        through_start_at=through_start_at,
        limit=FAST_HISTORY_LIMIT,
    )
    previous = {
        direction: storage.get_detector_state(
            ticker,
            RULE_ABRUPT_MOVE,
            MarketWindow.ONE_HOUR,
            direction,
        )
        for direction in SignalDirection
    }
    result = detect_fast_signals(bars, previous, now=now)
    _store_states(storage, result.states)
    return result


def detect_daily_from_storage(
    storage: SQLiteStorage,
    ticker: str,
    *,
    now: datetime,
    through_start_at: datetime | None = None,
) -> MarketDetectionResult:
    """Run the daily detector from persisted daily bars and state."""

    ticker = ticker.strip().upper()
    ticker_bars = storage.list_market_bars(
        ticker,
        MarketTimeframe.ONE_DAY,
        complete_only=True,
        through_start_at=through_start_at,
        limit=DAILY_HISTORY_LIMIT,
    )
    spy_bars = storage.list_market_bars(
        SPY_TICKER,
        MarketTimeframe.ONE_DAY,
        complete_only=True,
        through_start_at=through_start_at,
        limit=DAILY_HISTORY_LIMIT,
    )
    previous = {
        (rule, window, direction): storage.get_detector_state(
            ticker,
            rule,
            window,
            direction,
        )
        for rule, window in DAILY_RULE_WINDOWS
        for direction in SignalDirection
    }
    result = detect_daily_signals(ticker_bars, spy_bars, previous, now=now)
    _store_states(storage, result.states)
    return result


def market_direction_is_clear(
    storage: SQLiteStorage,
    ticker: str,
    direction: SignalDirection,
) -> bool:
    """Return whether every stored key for this direction is armed/clear."""

    states = storage.list_detector_states(ticker, direction)
    return all(state.last_emitted_importance is None for state in states)


def maintain_market_episodes(
    storage: SQLiteStorage,
    ticker: str,
    *,
    now: datetime,
) -> tuple[Event, ...]:
    """Close open market episodes whose directional detector keys are clear."""

    closed: list[Event] = []
    for direction in SignalDirection:
        if not market_direction_is_clear(storage, ticker, direction):
            continue
        for event in storage.find_direction_events(
            ticker,
            direction,
            with_market_signal=True,
        ):
            updated = storage.close_episode(event.event_id, closed_at=now)
            if updated is not None:
                closed.append(updated)
    return tuple(closed)


def _collect_signed_rule(
    latest: MarketBar,
    *,
    rule: str,
    window: MarketWindow,
    signed_value: Decimal,
    previous_states: Mapping[DailyStateKey, DetectorState | None],
    now: datetime,
    signals: list[MarketSignal],
    states: list[DetectorState],
    baseline_price: Decimal,
    comparison_return_ratio: Decimal | None,
) -> None:
    for direction in SignalDirection:
        _collect_one_key(
            latest,
            rule=rule,
            window=window,
            direction=direction,
            magnitude=directional_magnitude(signed_value, direction),
            previous_states=previous_states,
            now=now,
            signals=signals,
            states=states,
            baseline_price=baseline_price,
            comparison_return_ratio=comparison_return_ratio,
        )


def _collect_one_key(
    latest: MarketBar,
    *,
    rule: str,
    window: MarketWindow,
    direction: SignalDirection,
    magnitude: Decimal,
    previous_states: Mapping[DailyStateKey, DetectorState | None],
    now: datetime,
    signals: list[MarketSignal],
    states: list[DetectorState],
    baseline_price: Decimal,
    comparison_return_ratio: Decimal | None,
) -> None:
    previous = previous_states.get((rule, window, direction))
    if _key_already_evaluated(previous, latest.start_at):
        return
    candidate = thresholds_for(rule, window).importance_for(magnitude)
    decision = decide_crossing(
        ticker=latest.ticker,
        rule=rule,
        window=window,
        direction=direction,
        magnitude=magnitude,
        candidate_importance=candidate,
        previous=previous,
        now=now,
        evaluated_at=latest.start_at,
    )
    states.append(decision.next_state)
    if decision.emit_importance is not None:
        signals.append(
            _market_signal(
                latest,
                importance=decision.emit_importance,
                direction=direction,
                rule=rule,
                window=window,
                magnitude=magnitude,
                volume_ratio=UNUSED_VOLUME_RATIO,
                baseline_price=baseline_price,
                comparison_return_ratio=comparison_return_ratio,
            )
        )


def _market_signal(
    bar: MarketBar,
    *,
    importance: SignalImportance,
    direction: SignalDirection,
    rule: str,
    window: MarketWindow,
    magnitude: Decimal,
    volume_ratio: Decimal,
    baseline_price: Decimal,
    comparison_return_ratio: Decimal | None,
) -> MarketSignal:
    return MarketSignal(
        signal_id=stable_signal_id(
            "market",
            bar.ticker,
            rule,
            window.value,
            direction.value,
            bar.start_at.isoformat(),
        ),
        ticker=bar.ticker,
        occurred_at=bar.end_at,
        importance=importance,
        source_details=SourceDetails(
            provider=bar.provider,
            source=bar.provider,
            feed=bar.feed,
            retrieved_at=bar.retrieved_at,
        ),
        direction=direction,
        rule=rule,
        window=window,
        price_decline_ratio=magnitude,
        volume_ratio=volume_ratio,
        baseline_price=baseline_price,
        observed_price=bar.close,
        comparison_return_ratio=comparison_return_ratio,
    )


def _fast_volume(
    completed: Sequence[MarketBar],
) -> tuple[Decimal, Decimal | None]:
    latest = completed[-1]
    if len(completed) < FAST_VOLUME_LOOKBACK + 1:
        return UNUSED_VOLUME_RATIO, None
    prior = completed[-(FAST_VOLUME_LOOKBACK + 1) : -1]
    average = sum((bar.volume for bar in prior), start=Decimal("0")) / Decimal(
        FAST_VOLUME_LOOKBACK
    )
    if average == 0:
        return UNUSED_VOLUME_RATIO, None
    ratio = latest.volume / average
    return ratio, ratio


def _usable_bars(
    bars: Sequence[MarketBar],
    timeframe: MarketTimeframe,
) -> list[MarketBar]:
    usable = [bar for bar in bars if bar.is_complete and bar.timeframe is timeframe]
    usable.sort(key=lambda bar: (bar.start_at, bar.bar_id))
    if not usable:
        return []
    ticker = usable[-1].ticker
    return [bar for bar in usable if bar.ticker == ticker]


def _bar_on_date(bars: Sequence[MarketBar], day: date) -> MarketBar | None:
    matches = [bar for bar in bars if bar.start_at.date() == day]
    return matches[-1] if matches else None


def _state(
    ticker: str,
    rule: str,
    window: MarketWindow,
    direction: SignalDirection,
    *,
    last_emitted_importance: SignalImportance | None,
    now: datetime,
    evaluated_at: datetime | None,
) -> DetectorState:
    return DetectorState(
        ticker=ticker,
        rule=rule,
        window=window,
        direction=direction,
        last_emitted_importance=last_emitted_importance,
        updated_at=now,
        last_evaluated_at=evaluated_at,
    )


def _key_already_evaluated(
    state: DetectorState | None,
    evaluated_at: datetime,
) -> bool:
    return (
        state is not None
        and state.last_evaluated_at is not None
        and state.last_evaluated_at > evaluated_at
    )


def _store_states(
    storage: SQLiteStorage,
    states: Sequence[DetectorState],
) -> None:
    for state in states:
        storage.save_detector_state(state)
