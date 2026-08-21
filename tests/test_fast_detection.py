"""Tests for the fast one-hour detector on completed minute bars."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment_assistant.market_detection import (
    MarketDetectionResult,
    detect_fast_from_storage,
    detect_fast_signals,
)
from investment_assistant.market_metrics import RULE_ABRUPT_MOVE
from investment_assistant.models import (
    MarketBar,
    MarketSignal,
    MarketTimeframe,
    MarketWindow,
    SignalDirection,
    SignalImportance,
)
from investment_assistant.storage import SQLiteStorage

NOW = datetime(2026, 2, 2, 16, 31, tzinfo=UTC)
START = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)
BASE_CLOSE = Decimal("100")
STRONG_VOLUME = Decimal("2000")
BASE_VOLUME = Decimal("1000")


def _minute_bar(
    index: int,
    *,
    close: Decimal,
    volume: Decimal = BASE_VOLUME,
    is_complete: bool = True,
    ticker: str = "TSLA",
) -> MarketBar:
    start_at = START + timedelta(minutes=index)
    return MarketBar(
        ticker=ticker,
        timeframe=MarketTimeframe.ONE_MINUTE,
        start_at=start_at,
        end_at=start_at + timedelta(minutes=1),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        is_complete=is_complete,
        provider="fixture",
        feed="minute-bars",
        retrieved_at=start_at + timedelta(minutes=1),
    )


def _minute_at(start_at: datetime, *, close: Decimal) -> MarketBar:
    return MarketBar(
        ticker="TSLA",
        timeframe=MarketTimeframe.ONE_MINUTE,
        start_at=start_at,
        end_at=start_at + timedelta(minutes=1),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=STRONG_VOLUME,
        is_complete=True,
        provider="fixture",
        feed="minute-bars",
        retrieved_at=start_at + timedelta(minutes=1),
    )


def _series(
    last_close: Decimal,
    *,
    last_volume: Decimal = STRONG_VOLUME,
    count: int = 61,
    last_complete: bool = True,
) -> list[MarketBar]:
    bars = [
        _minute_bar(index, close=BASE_CLOSE, volume=BASE_VOLUME)
        for index in range(count - 1)
    ]
    bars.append(
        _minute_bar(
            count - 1,
            close=last_close,
            volume=last_volume,
            is_complete=last_complete,
        )
    )
    return bars


def _down_signal(result: MarketDetectionResult) -> MarketSignal:
    downs = [
        signal for signal in result.signals if signal.direction is SignalDirection.DOWN
    ]
    assert len(downs) == 1
    return downs[0]


def test_fast_detector_emits_moderate_one_hour_drop() -> None:
    result = detect_fast_signals(_series(Decimal("97")), {}, now=NOW)
    signal = _down_signal(result)

    assert signal.rule == RULE_ABRUPT_MOVE
    assert signal.window is MarketWindow.ONE_HOUR
    assert signal.importance is SignalImportance.MODERATE
    assert signal.price_decline_ratio == Decimal("0.03")
    assert signal.volume_ratio == Decimal("2")
    assert signal.baseline_price == Decimal("100")
    assert signal.observed_price == Decimal("97")
    assert signal.occurred_at == START + timedelta(minutes=61)


def test_fast_detector_signal_id_is_stable() -> None:
    bars = _series(Decimal("95"))
    first = detect_fast_signals(bars, {}, now=NOW)
    second = detect_fast_signals(bars, {}, now=NOW)

    assert first.signals[0].signal_id == second.signals[0].signal_id


def test_fast_detector_stays_quiet_below_threshold() -> None:
    result = detect_fast_signals(_series(Decimal("97.01")), {}, now=NOW)

    assert result.signals == ()


def test_fast_detector_needs_sixty_one_completed_minute_bars() -> None:
    result = detect_fast_signals(_series(Decimal("95"), count=60), {}, now=NOW)

    assert result.signals == ()


def test_fast_detector_ignores_incomplete_latest_bar() -> None:
    result = detect_fast_signals(
        _series(Decimal("95"), last_complete=False),
        {},
        now=NOW,
    )

    assert result.signals == ()


def test_weak_volume_dampens_moderate_to_no_emit() -> None:
    result = detect_fast_signals(
        _series(Decimal("97"), last_volume=BASE_VOLUME),
        {},
        now=NOW,
    )

    assert result.signals == ()


def test_weak_volume_lowers_high_to_moderate() -> None:
    result = detect_fast_signals(
        _series(Decimal("95"), last_volume=BASE_VOLUME),
        {},
        now=NOW,
    )
    signal = _down_signal(result)

    assert signal.importance is SignalImportance.MODERATE
    assert signal.volume_ratio == Decimal("1")


def test_continuation_at_same_severity_is_quiet() -> None:
    first = detect_fast_signals(_series(Decimal("97")), {}, now=NOW)
    states = {state.direction: state for state in first.states}
    second = detect_fast_signals(_series(Decimal("97")), states, now=NOW)

    assert first.signals[0].importance is SignalImportance.MODERATE
    assert second.signals == ()


def test_escalation_from_moderate_to_high_emits_again() -> None:
    first = detect_fast_signals(_series(Decimal("97")), {}, now=NOW)
    states = {state.direction: state for state in first.states}
    second = detect_fast_signals(_series(Decimal("95")), states, now=NOW)

    assert first.signals[0].importance is SignalImportance.MODERATE
    assert _down_signal(second).importance is SignalImportance.HIGH


def test_upside_move_uses_the_same_thresholds() -> None:
    result = detect_fast_signals(_series(Decimal("108")), {}, now=NOW)
    ups = [
        signal for signal in result.signals if signal.direction is SignalDirection.UP
    ]

    assert len(ups) == 1
    assert ups[0].importance is SignalImportance.CRITICAL
    assert ups[0].price_decline_ratio == Decimal("0.08")


def test_fast_detector_reads_completed_bars_from_storage(tmp_path: Path) -> None:
    with SQLiteStorage(tmp_path / "fast.sqlite3") as storage:
        storage.initialize()
        for bar in _series(Decimal("92")):
            storage.save_market_bar(bar)

        first = detect_fast_from_storage(storage, "tsla", now=NOW)
        replay = detect_fast_from_storage(storage, "TSLA", now=NOW)

    assert _down_signal(first).importance is SignalImportance.CRITICAL
    assert replay.signals == ()


def test_legacy_extended_hours_rows_cannot_fill_fast_history(tmp_path: Path) -> None:
    regular = _series(Decimal("100"), count=59)
    premarket_start = datetime(2026, 2, 3, 13, 0, tzinfo=UTC)
    premarket = [
        _minute_at(premarket_start + timedelta(minutes=index), close=Decimal("100"))
        for index in range(60)
    ]
    next_open = _minute_at(
        datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
        close=Decimal("97"),
    )

    with SQLiteStorage(tmp_path / "legacy-extended.sqlite3") as storage:
        storage.initialize()
        for bar in (*regular, *premarket, next_open):
            storage.save_market_bar(bar)

        result = detect_fast_from_storage(
            storage,
            "TSLA",
            now=next_open.end_at,
            through_start_at=next_open.start_at,
        )
        selected = storage.list_market_bars(
            "TSLA",
            MarketTimeframe.ONE_MINUTE,
            complete_only=True,
            regular_session_only=True,
            through_start_at=next_open.start_at,
            limit=61,
        )

    assert len(selected) == 60
    assert all(bar not in selected for bar in premarket)
    assert result.signals == ()
    assert result.states == ()


def test_older_as_of_evaluation_cannot_rewind_newer_state(tmp_path: Path) -> None:
    bars = _series(Decimal("92"), count=66)
    with SQLiteStorage(tmp_path / "stale-fast.sqlite3") as storage:
        storage.initialize()
        for bar in bars:
            storage.save_market_bar(bar)

        newest = detect_fast_from_storage(
            storage,
            "TSLA",
            now=NOW,
            through_start_at=bars[-1].start_at,
        )
        stale = detect_fast_from_storage(
            storage,
            "TSLA",
            now=NOW + timedelta(minutes=1),
            through_start_at=bars[-2].start_at,
        )
        state = storage.get_detector_state(
            "TSLA",
            RULE_ABRUPT_MOVE,
            MarketWindow.ONE_HOUR,
            SignalDirection.DOWN,
        )

    assert newest.signals
    assert stale.signals == ()
    assert stale.states == ()
    assert state is not None
    assert state.last_evaluated_at == bars[-1].start_at
    assert state.last_emitted_importance is SignalImportance.CRITICAL
