"""Tests for pure market metrics and inclusive threshold tables."""

from decimal import Decimal

import pytest

from investment_assistant.market_metrics import (
    RULE_ABRUPT_MOVE,
    RULE_DRAWDOWN_FROM_HIGH,
    RULE_MULTI_DAY_MOVE,
    RULE_RELATIVE_TO_SPY,
    VOLUME_SUPPORT_RATIO,
    dampen_importance,
    directional_magnitude,
    drawdown_from_high,
    rally_from_low,
    relative_return,
    signed_return,
    thresholds_for,
)
from investment_assistant.models import MarketWindow, SignalDirection, SignalImportance


@pytest.mark.parametrize(
    ("magnitude", "expected"),
    [
        (Decimal("0.029999"), None),
        (Decimal("0.03"), SignalImportance.MODERATE),
        (Decimal("0.049999"), SignalImportance.MODERATE),
        (Decimal("0.05"), SignalImportance.HIGH),
        (Decimal("0.079999"), SignalImportance.HIGH),
        (Decimal("0.08"), SignalImportance.CRITICAL),
        (Decimal("0.20"), SignalImportance.CRITICAL),
    ],
)
def test_fast_thresholds_are_inclusive(
    magnitude: Decimal,
    expected: SignalImportance | None,
) -> None:
    table = thresholds_for(RULE_ABRUPT_MOVE, MarketWindow.ONE_HOUR)

    assert table.importance_for(magnitude) is expected


@pytest.mark.parametrize(
    ("rule", "window", "moderate", "high", "critical", "rearm"),
    [
        (
            RULE_ABRUPT_MOVE,
            MarketWindow.ONE_HOUR,
            Decimal("0.03"),
            Decimal("0.05"),
            Decimal("0.08"),
            Decimal("0.015"),
        ),
        (
            RULE_MULTI_DAY_MOVE,
            MarketWindow.FIVE_DAYS,
            Decimal("0.05"),
            Decimal("0.08"),
            Decimal("0.12"),
            Decimal("0.025"),
        ),
        (
            RULE_MULTI_DAY_MOVE,
            MarketWindow.TWENTY_DAYS,
            Decimal("0.10"),
            Decimal("0.15"),
            Decimal("0.20"),
            Decimal("0.05"),
        ),
        (
            RULE_DRAWDOWN_FROM_HIGH,
            MarketWindow.TWENTY_DAYS,
            Decimal("0.08"),
            Decimal("0.12"),
            Decimal("0.18"),
            Decimal("0.04"),
        ),
        (
            RULE_RELATIVE_TO_SPY,
            MarketWindow.FIVE_DAYS,
            Decimal("0.05"),
            Decimal("0.08"),
            Decimal("0.12"),
            Decimal("0.025"),
        ),
        (
            RULE_RELATIVE_TO_SPY,
            MarketWindow.TWENTY_DAYS,
            Decimal("0.05"),
            Decimal("0.08"),
            Decimal("0.12"),
            Decimal("0.025"),
        ),
    ],
)
def test_threshold_tables_and_rearm_lines(
    rule: str,
    window: MarketWindow,
    moderate: Decimal,
    high: Decimal,
    critical: Decimal,
    rearm: Decimal,
) -> None:
    table = thresholds_for(rule, window)

    assert table.moderate == moderate
    assert table.high == high
    assert table.critical == critical
    assert table.rearm_line == rearm
    assert table.importance_for(moderate) is SignalImportance.MODERATE
    assert table.importance_for(moderate - Decimal("0.000001")) is None
    assert table.importance_for(high) is SignalImportance.HIGH
    assert table.importance_for(critical) is SignalImportance.CRITICAL


def test_signed_return_and_directional_magnitude() -> None:
    down = signed_return(Decimal("100"), Decimal("94"))
    up = signed_return(Decimal("100"), Decimal("106"))

    assert down == Decimal("-0.06")
    assert up == Decimal("0.06")
    assert directional_magnitude(down, SignalDirection.DOWN) == Decimal("0.06")
    assert directional_magnitude(down, SignalDirection.UP) == Decimal("0")
    assert directional_magnitude(up, SignalDirection.UP) == Decimal("0.06")
    assert directional_magnitude(up, SignalDirection.DOWN) == Decimal("0")


def test_drawdown_and_rally_are_zero_at_the_reference() -> None:
    assert drawdown_from_high(Decimal("100"), Decimal("100")) == Decimal("0")
    assert drawdown_from_high(Decimal("100"), Decimal("92")) == Decimal("0.08")
    assert rally_from_low(Decimal("100"), Decimal("100")) == Decimal("0")
    assert rally_from_low(Decimal("80"), Decimal("90")) == Decimal("0.125")


def test_relative_return_matches_spec_example() -> None:
    relative = relative_return(Decimal("-0.06"), Decimal("-0.01"))

    assert relative == Decimal("-0.05")
    assert directional_magnitude(relative, SignalDirection.DOWN) == Decimal("0.05")
    table = thresholds_for(RULE_RELATIVE_TO_SPY, MarketWindow.FIVE_DAYS)
    assert table.importance_for(Decimal("0.05")) is SignalImportance.MODERATE


@pytest.mark.parametrize(
    ("importance", "volume_ratio", "expected"),
    [
        (SignalImportance.CRITICAL, Decimal("1.5"), SignalImportance.CRITICAL),
        (SignalImportance.CRITICAL, VOLUME_SUPPORT_RATIO, SignalImportance.CRITICAL),
        (SignalImportance.CRITICAL, Decimal("1.499"), SignalImportance.HIGH),
        (SignalImportance.HIGH, Decimal("1.0"), SignalImportance.MODERATE),
        (SignalImportance.MODERATE, Decimal("1.0"), None),
        (SignalImportance.HIGH, None, SignalImportance.HIGH),
        (None, Decimal("1.0"), None),
    ],
)
def test_volume_dampening_lowers_one_step(
    importance: SignalImportance | None,
    volume_ratio: Decimal | None,
    expected: SignalImportance | None,
) -> None:
    assert dampen_importance(importance, volume_ratio) is expected


def test_unknown_rule_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="no thresholds"):
        thresholds_for(RULE_ABRUPT_MOVE, MarketWindow.FIVE_DAYS)
