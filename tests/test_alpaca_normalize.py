"""Tests for Alpaca REST and stream bar mapping. No network."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from investment_assistant.market_data import (
    StreamEventKind,
    market_bar_from_alpaca,
    regular_session_close,
    stream_minute_from_alpaca,
)
from investment_assistant.models import MarketTimeframe

RETRIEVED_AT = datetime(2026, 2, 2, 15, 31, 2, tzinfo=UTC)

REST_MINUTE_BAR: dict[str, object] = {
    "t": "2026-02-02T15:30:00Z",
    "o": 250.10,
    "h": 251.50,
    "l": 249.25,
    "c": 250.75,
    "v": 12000,
    "n": 84,
    "vw": 250.40,
}

STREAM_MINUTE_BAR: dict[str, object] = {
    "T": "b",
    "S": "SPY",
    "o": 388.985,
    "h": 389.13,
    "l": 388.975,
    "c": 389.12,
    "v": 49378,
    "n": 461,
    "vw": 389.062639,
    "t": "2021-02-22T19:15:00Z",
}


def test_rest_minute_bar_becomes_complete_market_bar() -> None:
    bar = market_bar_from_alpaca(
        REST_MINUTE_BAR,
        ticker="tsla",
        timeframe=MarketTimeframe.ONE_MINUTE,
        feed="iex",
        retrieved_at=RETRIEVED_AT,
    )

    assert bar.ticker == "TSLA"
    assert bar.timeframe is MarketTimeframe.ONE_MINUTE
    assert bar.start_at == datetime(2026, 2, 2, 15, 30, tzinfo=UTC)
    assert bar.end_at == datetime(2026, 2, 2, 15, 31, tzinfo=UTC)
    assert bar.open == Decimal("250.1")
    assert bar.high == Decimal("251.5")
    assert bar.low == Decimal("249.25")
    assert bar.close == Decimal("250.75")
    assert bar.volume == Decimal("12000")
    assert bar.is_complete is True
    assert bar.provider == "alpaca"
    assert bar.feed == "iex"
    assert bar.retrieved_at == RETRIEVED_AT
    assert bar.bar_id == "bar:TSLA:1Min:2026-02-02T15:30:00+00:00"


def test_rest_daily_bar_ends_at_regular_session_close() -> None:
    payload = {
        "t": "2026-01-20T05:00:00Z",
        "o": "100.00",
        "h": "101.00",
        "l": "99.50",
        "c": "100.25",
        "v": 1_000_000,
    }

    bar = market_bar_from_alpaca(
        payload,
        ticker="AMD",
        timeframe=MarketTimeframe.ONE_DAY,
        feed="sip",
        retrieved_at=datetime(2026, 1, 20, 21, 5, tzinfo=UTC),
    )

    assert bar.timeframe is MarketTimeframe.ONE_DAY
    assert bar.start_at == datetime(2026, 1, 20, 5, 0, tzinfo=UTC)
    assert bar.end_at == datetime(2026, 1, 20, 21, 0, tzinfo=UTC)
    assert bar.end_at == regular_session_close(bar.start_at)
    assert bar.is_complete is True
    assert bar.feed == "sip"
    assert bar.bar_id == "bar:AMD:1Day:2026-01-20T05:00:00+00:00"


def test_rest_daily_bar_is_unfinished_before_regular_close() -> None:
    payload = {
        "t": "2026-01-20T05:00:00Z",
        "o": "100.00",
        "h": "101.00",
        "l": "84.00",
        "c": "84.21",
        "v": 1_000_000,
    }

    bar = market_bar_from_alpaca(
        payload,
        ticker="AMD",
        timeframe=MarketTimeframe.ONE_DAY,
        feed="iex",
        retrieved_at=datetime(2026, 1, 20, 15, 25, tzinfo=UTC),
    )

    assert bar.end_at == datetime(2026, 1, 20, 21, 0, tzinfo=UTC)
    assert bar.is_complete is False


def test_daily_bar_close_follows_eastern_daylight_time() -> None:
    bar = market_bar_from_alpaca(
        {
            "t": "2026-07-01T04:00:00Z",
            "o": 1,
            "h": 1,
            "l": 1,
            "c": 1,
            "v": 1,
        },
        ticker="SPY",
        timeframe=MarketTimeframe.ONE_DAY,
        feed="iex",
        retrieved_at=datetime(2026, 7, 1, 20, 5, tzinfo=UTC),
    )

    assert bar.end_at == datetime(2026, 7, 1, 20, 0, tzinfo=UTC)


def test_stream_completed_minute_uses_symbol_and_utc_times() -> None:
    event = stream_minute_from_alpaca(
        STREAM_MINUTE_BAR,
        feed="iex",
        retrieved_at=datetime(2021, 2, 22, 19, 15, 1, tzinfo=UTC),
    )

    assert event.kind is StreamEventKind.BAR
    assert event.bar.ticker == "SPY"
    assert event.bar.start_at == datetime(2021, 2, 22, 19, 15, tzinfo=UTC)
    assert event.bar.end_at == datetime(2021, 2, 22, 19, 16, tzinfo=UTC)
    assert event.bar.is_complete is True
    assert event.bar.provider == "alpaca"


def test_updated_bar_keeps_the_same_bar_identity() -> None:
    updated = {**STREAM_MINUTE_BAR, "T": "u", "h": 389.20, "c": 389.20, "v": 50000}

    original = stream_minute_from_alpaca(
        STREAM_MINUTE_BAR,
        feed="iex",
        retrieved_at=RETRIEVED_AT,
    )
    revision = stream_minute_from_alpaca(
        updated,
        feed="iex",
        retrieved_at=RETRIEVED_AT + timedelta(seconds=30),
    )

    assert revision.kind is StreamEventKind.UPDATED_BAR
    assert revision.bar.bar_id == original.bar.bar_id
    assert revision.bar.close == Decimal("389.2")
    assert revision.bar.volume == Decimal("50000")
    assert revision.bar.is_complete is True


def test_offset_and_nanosecond_timestamps_become_utc() -> None:
    bar = market_bar_from_alpaca(
        {
            **REST_MINUTE_BAR,
            "t": "2026-02-02T10:30:00.123456789-05:00",
        },
        ticker="TSLA",
        timeframe=MarketTimeframe.ONE_MINUTE,
        feed="iex",
        retrieved_at=RETRIEVED_AT,
    )

    assert bar.start_at == datetime(2026, 2, 2, 15, 30, 0, 123456, tzinfo=UTC)


def test_running_daily_stream_bars_are_rejected() -> None:
    payload = {**STREAM_MINUTE_BAR, "T": "d"}

    with pytest.raises(ValueError, match="unsupported Alpaca bar message type"):
        market_bar_from_alpaca(
            payload,
            timeframe=MarketTimeframe.ONE_MINUTE,
            feed="iex",
            retrieved_at=RETRIEVED_AT,
        )
    with pytest.raises(ValueError, match="completed bar or updated bar"):
        stream_minute_from_alpaca(
            payload,
            feed="iex",
            retrieved_at=RETRIEVED_AT,
        )


def test_non_mapping_payload_is_rejected() -> None:
    class FakeSdkBar:
        timestamp = "2026-02-02T15:30:00Z"
        open = 1
        close = 1

    with pytest.raises(TypeError, match="must be a mapping"):
        market_bar_from_alpaca(
            FakeSdkBar(),  # type: ignore[arg-type]
            ticker="TSLA",
            timeframe=MarketTimeframe.ONE_MINUTE,
            feed="iex",
            retrieved_at=RETRIEVED_AT,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {key: value for key, value in REST_MINUTE_BAR.items() if key != "t"},
        {**REST_MINUTE_BAR, "o": 0},
        {**REST_MINUTE_BAR, "v": -1},
        {**REST_MINUTE_BAR, "t": "2026-02-02T15:30:00"},
    ],
)
def test_invalid_alpaca_payloads_are_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        market_bar_from_alpaca(
            payload,
            ticker="TSLA",
            timeframe=MarketTimeframe.ONE_MINUTE,
            feed="iex",
            retrieved_at=RETRIEVED_AT,
        )


def test_missing_ticker_is_rejected() -> None:
    with pytest.raises(ValueError, match="ticker is required"):
        market_bar_from_alpaca(
            REST_MINUTE_BAR,
            timeframe=MarketTimeframe.ONE_MINUTE,
            feed="iex",
            retrieved_at=RETRIEVED_AT,
        )
