"""Tests for the after-close daily market detector."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from investment_assistant.market_detection import (
    MarketDetectionResult,
    detect_daily_from_storage,
    detect_daily_signals,
)
from investment_assistant.market_metrics import (
    RULE_DRAWDOWN_FROM_HIGH,
    RULE_MULTI_DAY_MOVE,
    RULE_RELATIVE_TO_SPY,
)
from investment_assistant.models import (
    MarketBar,
    MarketSignal,
    MarketTimeframe,
    MarketWindow,
    SignalDirection,
    SignalImportance,
)
from investment_assistant.storage import SQLiteStorage

NOW = datetime(2026, 2, 20, 21, 5, tzinfo=UTC)
START = datetime(2026, 1, 20, 14, 30, tzinfo=UTC)


def _daily_bar(
    index: int,
    *,
    close: Decimal,
    high: Decimal | None = None,
    low: Decimal | None = None,
    ticker: str = "TSLA",
) -> MarketBar:
    start_at = START + timedelta(days=index)
    high_price = close if high is None else high
    low_price = close if low is None else low
    return MarketBar(
        ticker=ticker,
        timeframe=MarketTimeframe.ONE_DAY,
        start_at=start_at,
        end_at=start_at + timedelta(hours=6, minutes=30),
        open=close,
        high=high_price,
        low=low_price,
        close=close,
        volume=Decimal("1000000"),
        is_complete=True,
        provider="fixture",
        feed="daily-bars",
        retrieved_at=start_at + timedelta(hours=6, minutes=35),
    )


def _flat_then(
    last_close: Decimal,
    *,
    count: int,
    ticker: str = "TSLA",
    last_high: Decimal | None = None,
    last_low: Decimal | None = None,
    base: Decimal = Decimal("100"),
) -> list[MarketBar]:
    bars = [_daily_bar(index, close=base, ticker=ticker) for index in range(count - 1)]
    bars.append(
        _daily_bar(
            count - 1,
            close=last_close,
            high=last_high,
            low=last_low,
            ticker=ticker,
        )
    )
    return bars


def _signals_for(
    result: MarketDetectionResult,
    rule: str,
    window: MarketWindow,
    direction: SignalDirection = SignalDirection.DOWN,
) -> list[MarketSignal]:
    return [
        signal
        for signal in result.signals
        if signal.rule == rule
        and signal.window is window
        and signal.direction is direction
    ]


def test_five_day_move_maps_inclusive_thresholds() -> None:
    result = detect_daily_signals(
        _flat_then(Decimal("95"), count=6),
        [],
        {},
        now=NOW,
    )
    signals = _signals_for(result, RULE_MULTI_DAY_MOVE, MarketWindow.FIVE_DAYS)

    assert len(signals) == 1
    assert signals[0].importance is SignalImportance.MODERATE
    assert signals[0].price_decline_ratio == Decimal("0.05")


def test_five_day_move_stays_quiet_just_below_moderate() -> None:
    result = detect_daily_signals(
        _flat_then(Decimal("95.01"), count=6),
        [],
        {},
        now=NOW,
    )

    assert _signals_for(result, RULE_MULTI_DAY_MOVE, MarketWindow.FIVE_DAYS) == []


def test_twenty_day_move_emits_high() -> None:
    result = detect_daily_signals(
        _flat_then(Decimal("85"), count=21),
        [],
        {},
        now=NOW,
    )
    signals = _signals_for(result, RULE_MULTI_DAY_MOVE, MarketWindow.TWENTY_DAYS)

    assert len(signals) == 1
    assert signals[0].importance is SignalImportance.HIGH
    assert signals[0].price_decline_ratio == Decimal("0.15")


def test_drawdown_from_high_uses_lookback_high() -> None:
    bars = [
        _daily_bar(index, close=Decimal("100"), high=Decimal("100"))
        for index in range(19)
    ]
    bars.append(
        _daily_bar(
            19,
            close=Decimal("92"),
            high=Decimal("93"),
            low=Decimal("91"),
        )
    )

    result = detect_daily_signals(bars, [], {}, now=NOW)
    signals = _signals_for(result, RULE_DRAWDOWN_FROM_HIGH, MarketWindow.TWENTY_DAYS)

    assert len(signals) == 1
    assert signals[0].importance is SignalImportance.MODERATE
    assert signals[0].price_decline_ratio == Decimal("0.08")


def test_relative_to_spy_uses_same_horizon() -> None:
    ticker_bars = _flat_then(Decimal("94"), count=6)
    spy_bars = _flat_then(Decimal("99"), count=6, ticker="SPY")

    result = detect_daily_signals(ticker_bars, spy_bars, {}, now=NOW)
    signals = _signals_for(result, RULE_RELATIVE_TO_SPY, MarketWindow.FIVE_DAYS)

    assert len(signals) == 1
    assert signals[0].importance is SignalImportance.MODERATE
    assert signals[0].price_decline_ratio == Decimal("0.05")


def test_missing_spy_skips_only_relative_rules() -> None:
    result = detect_daily_signals(
        _flat_then(Decimal("95"), count=6),
        [],
        {},
        now=NOW,
    )

    assert _signals_for(result, RULE_MULTI_DAY_MOVE, MarketWindow.FIVE_DAYS)
    assert _signals_for(result, RULE_RELATIVE_TO_SPY, MarketWindow.FIVE_DAYS) == []
    assert any("missing SPY history" in note for note in result.diagnostics)
    assert any(MarketWindow.FIVE_DAYS.value in note for note in result.diagnostics)


def test_daily_continuation_is_quiet_and_escalation_emits() -> None:
    first = detect_daily_signals(
        _flat_then(Decimal("95"), count=6),
        [],
        {},
        now=NOW,
    )
    previous = {
        (state.rule, state.window, state.direction): state for state in first.states
    }
    continued = detect_daily_signals(
        _flat_then(Decimal("95"), count=6),
        [],
        previous,
        now=NOW,
    )
    escalated = detect_daily_signals(
        _flat_then(Decimal("88"), count=6),
        [],
        previous,
        now=NOW,
    )

    assert _signals_for(continued, RULE_MULTI_DAY_MOVE, MarketWindow.FIVE_DAYS) == []
    critical = _signals_for(escalated, RULE_MULTI_DAY_MOVE, MarketWindow.FIVE_DAYS)
    assert len(critical) == 1
    assert critical[0].importance is SignalImportance.CRITICAL


def test_daily_detector_persists_state_in_storage(tmp_path: Path) -> None:
    ticker_bars = _flat_then(Decimal("80"), count=21)
    spy_bars = _flat_then(Decimal("100"), count=21, ticker="SPY")

    with SQLiteStorage(tmp_path / "daily.sqlite3") as storage:
        storage.initialize()
        for bar in (*ticker_bars, *spy_bars):
            storage.save_market_bar(bar)

        first = detect_daily_from_storage(storage, "tsla", now=NOW)
        replay = detect_daily_from_storage(storage, "TSLA", now=NOW)

    twenty_day = _signals_for(first, RULE_MULTI_DAY_MOVE, MarketWindow.TWENTY_DAYS)
    relative = _signals_for(first, RULE_RELATIVE_TO_SPY, MarketWindow.TWENTY_DAYS)
    assert twenty_day[0].importance is SignalImportance.CRITICAL
    assert relative[0].importance is SignalImportance.CRITICAL
    assert replay.signals == ()
    assert first.diagnostics == ()
