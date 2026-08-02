"""Tests for deterministic market detection."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investment_assistant.detection import detect_market_signal
from investment_assistant.models import MarketRecord

BASE_RECORD = MarketRecord(
    symbol="ACME",
    latest_price=Decimal("94"),
    previous_close=Decimal("100"),
    current_volume=Decimal("160"),
    average_volume=Decimal("100"),
    occurred_at=datetime(2026, 1, 15, 15, 30, tzinfo=UTC),
    provider="fixture",
    feed="offline-demo",
)


def test_detects_qualifying_market_signal() -> None:
    signal = detect_market_signal(BASE_RECORD, tracked_symbol=" acme ")

    assert signal is not None
    assert signal.symbol == "ACME"
    assert signal.occurred_at == BASE_RECORD.occurred_at
    assert signal.price_decline_ratio == Decimal("0.06")
    assert signal.volume_ratio == Decimal("1.6")
    assert signal.provider == "fixture"
    assert signal.feed == "offline-demo"


def test_market_rule_includes_exact_thresholds() -> None:
    record = replace(
        BASE_RECORD,
        latest_price=Decimal("95"),
        current_volume=Decimal("150"),
    )

    signal = detect_market_signal(record, tracked_symbol="ACME")

    assert signal is not None
    assert signal.price_decline_ratio == Decimal("0.05")
    assert signal.volume_ratio == Decimal("1.5")


@pytest.mark.parametrize(
    ("record", "tracked_symbol"),
    [
        (replace(BASE_RECORD, latest_price=Decimal("95.01")), "ACME"),
        (replace(BASE_RECORD, current_volume=Decimal("149.99")), "ACME"),
        (BASE_RECORD, "OTHER"),
    ],
)
def test_rejects_non_qualifying_market_record(
    record: MarketRecord,
    tracked_symbol: str,
) -> None:
    assert detect_market_signal(record, tracked_symbol=tracked_symbol) is None


def test_rejects_blank_tracked_symbol() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        detect_market_signal(BASE_RECORD, tracked_symbol="   ")
