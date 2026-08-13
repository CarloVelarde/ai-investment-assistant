"""Pure market-move metrics and importance thresholds.

Thresholds are inclusive at the listed magnitude. Rearm is strictly below
half of the ``MODERATE`` magnitude for that rule and window.
"""

from dataclasses import dataclass
from decimal import Decimal

from investment_assistant.models import MarketWindow, SignalDirection, SignalImportance

RULE_ABRUPT_MOVE = "abrupt_move"
RULE_MULTI_DAY_MOVE = "multi_day_move"
RULE_DRAWDOWN_FROM_HIGH = "drawdown_from_high"
RULE_RELATIVE_TO_SPY = "relative_to_spy"

FAST_LOOKBACK_BARS = 60
FAST_VOLUME_LOOKBACK = 20
DRAWDOWN_LOOKBACK_BARS = 20
MULTI_DAY_LOOKBACKS = {
    MarketWindow.FIVE_DAYS: 5,
    MarketWindow.TWENTY_DAYS: 20,
}
VOLUME_SUPPORT_RATIO = Decimal("1.5")
UNUSED_VOLUME_RATIO = Decimal("1")


@dataclass(frozen=True, slots=True)
class ImportanceThresholds:
    """Inclusive magnitude bands that map a move onto importance."""

    moderate: Decimal
    high: Decimal
    critical: Decimal

    @property
    def rearm_line(self) -> Decimal:
        """Return half the moderate magnitude."""

        return self.moderate / 2

    def importance_for(self, magnitude: Decimal) -> SignalImportance | None:
        """Return the highest importance the magnitude meets, if any."""

        if magnitude < 0:
            raise ValueError("magnitude must not be negative")
        if magnitude >= self.critical:
            return SignalImportance.CRITICAL
        if magnitude >= self.high:
            return SignalImportance.HIGH
        if magnitude >= self.moderate:
            return SignalImportance.MODERATE
        return None


_THRESHOLDS: dict[tuple[str, MarketWindow], ImportanceThresholds] = {
    (RULE_ABRUPT_MOVE, MarketWindow.ONE_HOUR): ImportanceThresholds(
        moderate=Decimal("0.03"),
        high=Decimal("0.05"),
        critical=Decimal("0.08"),
    ),
    (RULE_MULTI_DAY_MOVE, MarketWindow.FIVE_DAYS): ImportanceThresholds(
        moderate=Decimal("0.05"),
        high=Decimal("0.08"),
        critical=Decimal("0.12"),
    ),
    (RULE_MULTI_DAY_MOVE, MarketWindow.TWENTY_DAYS): ImportanceThresholds(
        moderate=Decimal("0.10"),
        high=Decimal("0.15"),
        critical=Decimal("0.20"),
    ),
    (RULE_DRAWDOWN_FROM_HIGH, MarketWindow.TWENTY_DAYS): ImportanceThresholds(
        moderate=Decimal("0.08"),
        high=Decimal("0.12"),
        critical=Decimal("0.18"),
    ),
    (RULE_RELATIVE_TO_SPY, MarketWindow.FIVE_DAYS): ImportanceThresholds(
        moderate=Decimal("0.05"),
        high=Decimal("0.08"),
        critical=Decimal("0.12"),
    ),
    (RULE_RELATIVE_TO_SPY, MarketWindow.TWENTY_DAYS): ImportanceThresholds(
        moderate=Decimal("0.05"),
        high=Decimal("0.08"),
        critical=Decimal("0.12"),
    ),
}


def thresholds_for(rule: str, window: MarketWindow) -> ImportanceThresholds:
    """Return the threshold table for one rule and window."""

    try:
        return _THRESHOLDS[(rule, window)]
    except KeyError:
        raise ValueError(f"no thresholds for {rule} / {window.value}") from None


def signed_return(start_close: Decimal, end_close: Decimal) -> Decimal:
    """Return the signed close-to-close change ratio."""

    return (end_close - start_close) / start_close


def drawdown_from_high(reference_high: Decimal, close: Decimal) -> Decimal:
    """Return downside distance from a reference high, or zero if none."""

    if reference_high <= 0:
        raise ValueError("reference_high must be positive")
    if close >= reference_high:
        return Decimal("0")
    return (reference_high - close) / reference_high


def rally_from_low(reference_low: Decimal, close: Decimal) -> Decimal:
    """Return upside distance from a reference low, or zero if none."""

    if reference_low <= 0:
        raise ValueError("reference_low must be positive")
    if close <= reference_low:
        return Decimal("0")
    return (close - reference_low) / reference_low


def relative_return(ticker_return: Decimal, spy_return: Decimal) -> Decimal:
    """Return ticker return minus ``SPY`` return over the same horizon."""

    return ticker_return - spy_return


def directional_magnitude(
    signed_value: Decimal,
    direction: SignalDirection,
) -> Decimal:
    """Return the non-negative size of a signed move in one direction."""

    if direction is SignalDirection.DOWN:
        return -signed_value if signed_value < 0 else Decimal("0")
    return signed_value if signed_value > 0 else Decimal("0")


def dampen_importance(
    importance: SignalImportance | None,
    volume_ratio: Decimal | None,
) -> SignalImportance | None:
    """Lower fast-rule importance one step when volume is known and weak."""

    if importance is None or volume_ratio is None:
        return importance
    if volume_ratio >= VOLUME_SUPPORT_RATIO:
        return importance
    if importance is SignalImportance.CRITICAL:
        return SignalImportance.HIGH
    if importance is SignalImportance.HIGH:
        return SignalImportance.MODERATE
    return None
