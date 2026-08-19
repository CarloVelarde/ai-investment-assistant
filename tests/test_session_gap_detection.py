"""Tests for the session-open gap detector."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from investment_assistant.market_detection import detect_session_gap_signals
from investment_assistant.market_metrics import RULE_SESSION_GAP
from investment_assistant.models import (
    MarketBar,
    MarketTimeframe,
    MarketWindow,
    SignalDirection,
    SignalImportance,
)

MONDAY_OPEN = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)
NOW = MONDAY_OPEN + timedelta(minutes=1)


def _daily(day: date, close: Decimal) -> MarketBar:
    start_at = datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC)
    return MarketBar(
        ticker="AMD",
        timeframe=MarketTimeframe.ONE_DAY,
        start_at=start_at,
        end_at=start_at + timedelta(hours=6, minutes=30),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1000000"),
        is_complete=True,
        provider="alpaca",
        feed="iex",
        retrieved_at=start_at + timedelta(hours=6, minutes=35),
    )


def _opening(open_price: Decimal, *, start_at: datetime = MONDAY_OPEN) -> MarketBar:
    return MarketBar(
        ticker="AMD",
        timeframe=MarketTimeframe.ONE_MINUTE,
        start_at=start_at,
        end_at=start_at + timedelta(minutes=1),
        open=open_price,
        high=open_price + Decimal("1"),
        low=open_price - Decimal("1"),
        close=open_price - Decimal("0.50"),
        volume=Decimal("1"),
        is_complete=True,
        provider="alpaca",
        feed="iex",
        retrieved_at=start_at + timedelta(minutes=1),
    )


def test_session_gap_emits_high_up_for_450_to_480_open() -> None:
    result = detect_session_gap_signals(
        _daily(date(2026, 1, 30), Decimal("450")),
        _opening(Decimal("480")),
        {},
        now=NOW,
    )

    assert len(result.signals) == 1
    signal = result.signals[0]
    assert signal.rule == RULE_SESSION_GAP
    assert signal.window is MarketWindow.SESSION_OPEN
    assert signal.direction is SignalDirection.UP
    assert signal.importance is SignalImportance.HIGH
    assert signal.price_decline_ratio == Decimal("30") / Decimal("450")
    assert signal.baseline_price == Decimal("450")
    assert signal.observed_price == Decimal("480")
    assert signal.volume_ratio == Decimal("1")


def test_session_gap_stays_quiet_for_two_percent() -> None:
    result = detect_session_gap_signals(
        _daily(date(2026, 1, 30), Decimal("100")),
        _opening(Decimal("102")),
        {},
        now=NOW,
    )

    assert result.signals == ()


def test_session_gap_emits_moderate_down_at_inclusive_three_percent() -> None:
    result = detect_session_gap_signals(
        _daily(date(2026, 1, 30), Decimal("100")),
        _opening(Decimal("97")),
        {},
        now=NOW,
    )

    assert len(result.signals) == 1
    assert result.signals[0].direction is SignalDirection.DOWN
    assert result.signals[0].importance is SignalImportance.MODERATE
    assert result.signals[0].price_decline_ratio == Decimal("0.03")


def test_session_gap_uses_friday_close_for_monday_open() -> None:
    result = detect_session_gap_signals(
        _daily(date(2026, 1, 30), Decimal("450")),
        _opening(Decimal("480")),
        {},
        now=NOW,
    )

    assert len(result.signals) == 1
    assert result.signals[0].importance is SignalImportance.HIGH


def test_session_gap_rearms_only_below_one_and_a_half_percent() -> None:
    monday = detect_session_gap_signals(
        _daily(date(2026, 1, 30), Decimal("100")),
        _opening(Decimal("97")),
        {},
        now=NOW,
    )
    monday_states = {state.direction: state for state in monday.states}
    tuesday_open = MONDAY_OPEN + timedelta(days=1)
    at_rearm_line = detect_session_gap_signals(
        _daily(date(2026, 2, 2), Decimal("100")),
        _opening(Decimal("98.5"), start_at=tuesday_open),
        monday_states,
        now=tuesday_open + timedelta(minutes=1),
    )
    tuesday_states = {state.direction: state for state in at_rearm_line.states}
    wednesday_open = MONDAY_OPEN + timedelta(days=2)
    below_rearm_line = detect_session_gap_signals(
        _daily(date(2026, 2, 3), Decimal("100")),
        _opening(Decimal("98.51"), start_at=wednesday_open),
        tuesday_states,
        now=wednesday_open + timedelta(minutes=1),
    )
    wednesday_states = {state.direction: state for state in below_rearm_line.states}
    thursday_open = MONDAY_OPEN + timedelta(days=3)
    crossed_again = detect_session_gap_signals(
        _daily(date(2026, 2, 4), Decimal("100")),
        _opening(Decimal("97"), start_at=thursday_open),
        wednesday_states,
        now=thursday_open + timedelta(minutes=1),
    )

    assert monday.signals[0].importance is SignalImportance.MODERATE
    assert at_rearm_line.signals == ()
    assert (
        tuesday_states[SignalDirection.DOWN].last_emitted_importance
        is SignalImportance.MODERATE
    )
    assert below_rearm_line.signals == ()
    assert wednesday_states[SignalDirection.DOWN].last_emitted_importance is None
    assert crossed_again.signals[0].importance is SignalImportance.MODERATE
