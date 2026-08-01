"""Tests for deterministic signal correlation."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from investment_assistant.clock import FixedClock
from investment_assistant.correlation import correlate_signals
from investment_assistant.models import MarketSignal, NewsSignal

MARKET_TIME = datetime(2026, 1, 15, 15, 30, tzinfo=UTC)
EVENT_TIME = datetime(2026, 1, 15, 16, 30, tzinfo=UTC)
MARKET_SIGNAL = MarketSignal(
    symbol="ACME",
    occurred_at=MARKET_TIME,
    price_decline_ratio=Decimal("0.06"),
    volume_ratio=Decimal("1.6"),
    provider="fixture",
    feed="offline-demo",
)
NEWS_SIGNAL = NewsSignal(
    symbol="ACME",
    occurred_at=MARKET_TIME + timedelta(minutes=15),
    matched_phrase="guidance cut",
    headline="Acme announces guidance cut",
    source="Fixture Wire",
)
FIXED_CLOCK = FixedClock(EVENT_TIME)


def test_correlates_one_same_symbol_event() -> None:
    event = correlate_signals(MARKET_SIGNAL, NEWS_SIGNAL, clock=FIXED_CLOCK)

    assert event is not None
    assert event.symbol == "ACME"
    assert event.occurred_at == EVENT_TIME
    assert event.market_signal is MARKET_SIGNAL
    assert event.news_signal is NEWS_SIGNAL


@pytest.mark.parametrize("minute_offset", [-60, -30, 0, 30, 60])
def test_correlates_signals_inside_inclusive_window(minute_offset: int) -> None:
    news_signal = replace(
        NEWS_SIGNAL,
        occurred_at=MARKET_TIME + timedelta(minutes=minute_offset),
    )

    assert correlate_signals(MARKET_SIGNAL, news_signal, clock=FIXED_CLOCK) is not None


@pytest.mark.parametrize("minute_offset", [-61, 61])
def test_rejects_signals_outside_window(minute_offset: int) -> None:
    news_signal = replace(
        NEWS_SIGNAL,
        occurred_at=MARKET_TIME + timedelta(minutes=minute_offset),
    )

    assert correlate_signals(MARKET_SIGNAL, news_signal, clock=FIXED_CLOCK) is None


def test_rejects_symbol_mismatch() -> None:
    news_signal = replace(NEWS_SIGNAL, symbol="OTHER")

    assert correlate_signals(MARKET_SIGNAL, news_signal, clock=FIXED_CLOCK) is None


@pytest.mark.parametrize(
    ("market_signal", "news_signal"),
    [(None, NEWS_SIGNAL), (MARKET_SIGNAL, None), (None, None)],
)
def test_requires_both_signals(
    market_signal: MarketSignal | None,
    news_signal: NewsSignal | None,
) -> None:
    assert correlate_signals(market_signal, news_signal, clock=FIXED_CLOCK) is None
