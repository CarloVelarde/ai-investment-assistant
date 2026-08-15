"""Tests for the market-data port and in-memory fake. No network."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from investment_assistant.market_data import (
    FakeMarketData,
    HistoryPage,
    MarketData,
    MarketDataError,
    MarketSession,
    StreamEventKind,
    StreamMinute,
    market_bar_from_alpaca,
    stream_minute_from_alpaca,
)
from investment_assistant.models import MarketBar, MarketTimeframe

RETRIEVED_AT = datetime(2026, 2, 2, 15, 31, tzinfo=UTC)
SESSION = MarketSession(
    is_open=True,
    timestamp=datetime(2026, 2, 2, 15, 30, tzinfo=UTC),
    next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
    next_close=datetime(2026, 2, 2, 21, 0, tzinfo=UTC),
)


def _minute(
    ticker: str,
    start_at: datetime,
    close: str = "100.00",
) -> MarketBar:
    return market_bar_from_alpaca(
        {
            "t": start_at.isoformat().replace("+00:00", "Z"),
            "o": close,
            "h": close,
            "l": close,
            "c": close,
            "v": 1000,
        },
        ticker=ticker,
        timeframe=MarketTimeframe.ONE_MINUTE,
        feed="iex",
        retrieved_at=start_at + timedelta(minutes=1),
    )


def _daily(ticker: str, start_at: datetime, close: str = "100.00") -> MarketBar:
    return market_bar_from_alpaca(
        {
            "t": start_at.isoformat().replace("+00:00", "Z"),
            "o": close,
            "h": close,
            "l": close,
            "c": close,
            "v": 1_000_000,
        },
        ticker=ticker,
        timeframe=MarketTimeframe.ONE_DAY,
        feed="iex",
        retrieved_at=start_at + timedelta(hours=16),
    )


def _collect_history(
    provider: MarketData,
    *,
    symbols: Sequence[str],
    timeframe: MarketTimeframe,
    start: datetime,
    end: datetime,
) -> tuple[MarketBar, ...]:
    bars: list[MarketBar] = []
    page_token: str | None = None
    while True:
        page = provider.fetch_history(
            symbols=symbols,
            timeframe=timeframe,
            start=start,
            end=end,
            page_token=page_token,
        )
        bars.extend(page.bars)
        if page.next_page_token is None:
            break
        page_token = page.next_page_token
    return tuple(bars)


def test_session_times_are_normalized_to_utc() -> None:
    eastern = timezone(timedelta(hours=-5))
    session = MarketSession(
        is_open=False,
        timestamp=datetime(2026, 2, 2, 16, 0, tzinfo=eastern),
        next_open=datetime(2026, 2, 3, 9, 30, tzinfo=eastern),
        next_close=datetime(2026, 2, 3, 16, 0, tzinfo=eastern),
    )

    assert session.timestamp == datetime(2026, 2, 2, 21, 0, tzinfo=UTC)
    assert session.next_open == datetime(2026, 2, 3, 14, 30, tzinfo=UTC)
    assert session.next_close == datetime(2026, 2, 3, 21, 0, tzinfo=UTC)
    assert session.is_open is False


def test_fake_reports_open_and_closed_sessions() -> None:
    provider: MarketData = FakeMarketData(session=SESSION)

    assert provider.get_session().is_open is True

    fake = FakeMarketData(session=SESSION)
    fake.set_session(
        MarketSession(
            is_open=False,
            timestamp=datetime(2026, 2, 2, 21, 1, tzinfo=UTC),
            next_open=SESSION.next_open,
            next_close=SESSION.next_close,
        )
    )

    assert fake.get_session().is_open is False


def test_fake_session_is_required() -> None:
    with pytest.raises(MarketDataError, match="session is not available"):
        FakeMarketData().get_session()


def test_fake_history_pages_filter_and_paginate() -> None:
    start = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)
    tsla = [_minute("TSLA", start + timedelta(minutes=offset)) for offset in range(3)]
    other = _minute("AMD", start)
    daily = _daily("TSLA", datetime(2026, 2, 2, 5, 0, tzinfo=UTC))
    provider = FakeMarketData(
        history=(*tsla, other, daily),
        session=SESSION,
        page_size=2,
    )

    first = provider.fetch_history(
        symbols=("tsla",),
        timeframe=MarketTimeframe.ONE_MINUTE,
        start=start,
        end=start + timedelta(minutes=2),
    )
    second = provider.fetch_history(
        symbols=("TSLA",),
        timeframe=MarketTimeframe.ONE_MINUTE,
        start=start,
        end=start + timedelta(minutes=2),
        page_token=first.next_page_token,
    )

    assert [bar.start_at for bar in first.bars] == [
        start,
        start + timedelta(minutes=1),
    ]
    assert first.next_page_token == "2"
    assert [bar.start_at for bar in second.bars] == [start + timedelta(minutes=2)]
    assert second.next_page_token is None
    assert all(bar.ticker == "TSLA" for bar in (*first.bars, *second.bars))
    assert all(bar.timeframe is MarketTimeframe.ONE_MINUTE for bar in first.bars)


def test_history_consumer_uses_only_the_port() -> None:
    start = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)
    provider: MarketData = FakeMarketData(
        history=(
            _minute("TSLA", start, "101.00"),
            _minute("TSLA", start + timedelta(minutes=1), "99.00"),
        ),
        session=SESSION,
        page_size=1,
    )

    bars = _collect_history(
        provider,
        symbols=("TSLA",),
        timeframe=MarketTimeframe.ONE_MINUTE,
        start=start,
        end=start + timedelta(minutes=1),
    )

    assert len(bars) == 2
    assert [bar.close for bar in bars] == [Decimal("101.00"), Decimal("99.00")]
    assert all(isinstance(bar, MarketBar) for bar in bars)
    assert all(isinstance(page, HistoryPage) for page in [HistoryPage(bars=bars)])


def test_fake_stream_emits_completed_and_updated_minutes() -> None:
    payload = {
        "T": "b",
        "S": "TSLA",
        "o": 250.0,
        "h": 250.0,
        "l": 249.0,
        "c": 249.5,
        "v": 1000,
        "t": "2026-02-02T15:30:00Z",
    }
    first = stream_minute_from_alpaca(
        payload,
        feed="iex",
        retrieved_at=RETRIEVED_AT,
    )
    revision = stream_minute_from_alpaca(
        {**payload, "T": "u", "c": 249.0, "v": 1100},
        feed="iex",
        retrieved_at=RETRIEVED_AT + timedelta(seconds=30),
    )
    provider: MarketData = FakeMarketData(stream=(first,), session=SESSION)
    fake = FakeMarketData(session=SESSION)
    fake.push_stream(first, revision)

    events = list(provider.iter_stream_minutes())
    pushed = list(fake.iter_stream_minutes())

    assert events == [first]
    assert [event.kind for event in pushed] == [
        StreamEventKind.BAR,
        StreamEventKind.UPDATED_BAR,
    ]
    assert pushed[0].bar.bar_id == pushed[1].bar.bar_id
    assert isinstance(pushed[0], StreamMinute)
