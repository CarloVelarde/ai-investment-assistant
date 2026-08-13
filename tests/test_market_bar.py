"""Tests for normalized market bars used by history and detectors."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from investment_assistant.models import MarketBar, MarketTimeframe

START_AT = datetime(2026, 2, 2, 15, 30, tzinfo=UTC)
COMPLETE_BAR = MarketBar(
    ticker="tsla",
    timeframe=MarketTimeframe.ONE_MINUTE,
    start_at=START_AT,
    end_at=START_AT + timedelta(minutes=1),
    open=Decimal("250.00"),
    high=Decimal("251.50"),
    low=Decimal("249.25"),
    close=Decimal("250.75"),
    volume=Decimal("12000"),
    is_complete=True,
    provider="fixture-provider",
    feed="minute-bars",
    retrieved_at=START_AT + timedelta(seconds=5),
)


def test_complete_bar_normalizes_ticker_times_and_stable_id() -> None:
    central = timezone(-timedelta(hours=6))
    bar = replace(
        COMPLETE_BAR,
        ticker=" tsla ",
        start_at=datetime(2026, 2, 2, 9, 30, tzinfo=central),
        end_at=datetime(2026, 2, 2, 9, 31, tzinfo=central),
        retrieved_at=datetime(2026, 2, 2, 9, 31, 5, tzinfo=central),
    )

    assert bar.ticker == "TSLA"
    assert bar.start_at == START_AT
    assert bar.end_at == START_AT + timedelta(minutes=1)
    assert bar.retrieved_at == START_AT + timedelta(seconds=65)
    assert bar.is_complete is True
    assert bar.bar_id == "bar:TSLA:1Min:2026-02-02T15:30:00+00:00"


def test_incomplete_bar_is_allowed_when_ohlcv_is_valid() -> None:
    bar = replace(COMPLETE_BAR, is_complete=False)

    assert bar.is_complete is False
    assert bar.bar_id == COMPLETE_BAR.bar_id


def test_daily_spy_bar_uses_day_timeframe_in_identity() -> None:
    start_at = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)
    bar = replace(
        COMPLETE_BAR,
        ticker="spy",
        timeframe=MarketTimeframe.ONE_DAY,
        start_at=start_at,
        end_at=start_at + timedelta(hours=6, minutes=30),
    )

    assert bar.ticker == "SPY"
    assert bar.timeframe is MarketTimeframe.ONE_DAY
    assert bar.bar_id == "bar:SPY:1Day:2026-02-02T14:30:00+00:00"


@pytest.mark.parametrize(
    "build_bar",
    [
        lambda: replace(COMPLETE_BAR, ticker=" "),
        lambda: replace(COMPLETE_BAR, provider=" "),
        lambda: replace(COMPLETE_BAR, feed=" "),
        lambda: replace(COMPLETE_BAR, start_at=datetime(2026, 2, 2, 15, 30)),
        lambda: replace(
            COMPLETE_BAR,
            end_at=datetime(2026, 2, 2, 15, 31),
        ),
        lambda: replace(COMPLETE_BAR, retrieved_at=datetime(2026, 2, 2, 15, 30)),
        lambda: replace(COMPLETE_BAR, end_at=START_AT),
        lambda: replace(COMPLETE_BAR, end_at=START_AT - timedelta(minutes=1)),
        lambda: replace(COMPLETE_BAR, open=Decimal("0")),
        lambda: replace(COMPLETE_BAR, high=Decimal("-1")),
        lambda: replace(COMPLETE_BAR, volume=Decimal("-1")),
        lambda: replace(COMPLETE_BAR, high=Decimal("249.00")),
        lambda: replace(COMPLETE_BAR, low=Decimal("252.00")),
    ],
)
def test_market_bar_rejects_invalid_values(build_bar: Callable[[], MarketBar]) -> None:
    with pytest.raises(ValueError):
        build_bar()
