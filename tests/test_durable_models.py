"""Tests for normalized durable signal and event values."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from investment_assistant.models import (
    MarketSignal,
    MarketWindow,
    NewsSignal,
    SignalDirection,
    SignalImportance,
    SourceDetails,
)

OCCURRED_AT = datetime(2026, 2, 2, 15, 30, tzinfo=UTC)
SOURCE_DETAILS = SourceDetails(
    provider="fixture",
    source="offline scenario",
    feed="minute-bars",
    retrieved_at=OCCURRED_AT,
)
MARKET_SIGNAL = MarketSignal(
    signal_id="market-acme-1",
    ticker="acme",
    occurred_at=OCCURRED_AT,
    importance=SignalImportance.HIGH,
    source_details=SOURCE_DETAILS,
    direction=SignalDirection.DOWN,
    rule="abrupt-decline",
    window=MarketWindow.ONE_HOUR,
    price_decline_ratio=Decimal("0.06"),
    volume_ratio=Decimal("1.7"),
)


def test_market_signal_normalizes_ticker_and_time() -> None:
    central = timezone(-timedelta(hours=6))
    signal = replace(
        MARKET_SIGNAL,
        ticker=" tsla ",
        occurred_at=datetime(2026, 2, 2, 9, 30, tzinfo=central),
    )

    assert signal.ticker == "TSLA"
    assert signal.occurred_at == OCCURRED_AT
    assert signal.symbol == "TSLA"
    assert signal.provider == "fixture"
    assert signal.feed == "minute-bars"


def test_news_direction_can_be_unknown_without_losing_provenance() -> None:
    signal = NewsSignal(
        signal_id="news-acme-1",
        ticker="acme",
        occurred_at=OCCURRED_AT,
        importance=SignalImportance.MODERATE,
        source_details=SOURCE_DETAILS,
        category="LEADERSHIP",
        direction=None,
        headline="Acme names an interim executive",
        matched_phrase="interim executive",
    )

    assert signal.ticker == "ACME"
    assert signal.direction is None
    assert signal.source == "offline scenario"
    assert signal.source_details.retrieved_at == OCCURRED_AT


def test_importance_has_one_explicit_order() -> None:
    assert SignalImportance.MODERATE.rank < SignalImportance.HIGH.rank
    assert SignalImportance.HIGH.rank < SignalImportance.CRITICAL.rank


@pytest.mark.parametrize(
    "build_signal",
    [
        lambda: replace(MARKET_SIGNAL, signal_id=" "),
        lambda: replace(MARKET_SIGNAL, ticker=" "),
        lambda: replace(
            MARKET_SIGNAL,
            occurred_at=datetime(2026, 2, 2, 15, 30),
        ),
        lambda: replace(MARKET_SIGNAL, rule=" "),
        lambda: replace(
            MARKET_SIGNAL,
            price_decline_ratio=Decimal("-0.01"),
        ),
        lambda: replace(MARKET_SIGNAL, volume_ratio=Decimal("-0.01")),
    ],
)
def test_market_signal_rejects_invalid_values(
    build_signal: Callable[[], MarketSignal],
) -> None:
    with pytest.raises(ValueError):
        build_signal()


@pytest.mark.parametrize(
    "build_source",
    [
        lambda: replace(SOURCE_DETAILS, provider=" "),
        lambda: replace(SOURCE_DETAILS, source=" "),
        lambda: replace(SOURCE_DETAILS, feed=" "),
    ],
)
def test_source_details_reject_blank_values(
    build_source: Callable[[], SourceDetails],
) -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        build_source()
