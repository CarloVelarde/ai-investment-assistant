"""Tests for Alpaca stock-stream handshake and frame mapping. No network."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from investment_assistant.alpaca import AlpacaMarketData, HistoryHttpResponse
from investment_assistant.clock import FixedClock, SteppingClock
from investment_assistant.event_manager import EventManager
from investment_assistant.live_ingest import ingest_stream_minutes
from investment_assistant.market_data import StreamEventKind
from investment_assistant.models import MarketBar, MarketTimeframe
from investment_assistant.pipeline import process_market_bar
from investment_assistant.reporting import create_fake_research_report
from investment_assistant.stock_stream import (
    FakeStockStreamTransport,
    StockStreamAuthError,
)
from investment_assistant.storage import SQLiteStorage

SECRET = "test-alpaca-stream-secret-do-not-log"
RETRIEVED_AT = datetime(2026, 2, 2, 15, 31, tzinfo=UTC)
SESSION_OPEN = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)
WATCHLIST = ("TSLA", "SPY")


class ScriptedHistoryHttp:
    def get_stock_bars(self, params: Mapping[str, str]) -> HistoryHttpResponse:
        del params
        return HistoryHttpResponse(status_code=200, body={"bars": {}})


def _handshake(
    symbols: tuple[str, ...] = WATCHLIST,
) -> list[tuple[dict[str, object], ...]]:
    return [
        ({"T": "success", "msg": "connected"},),
        ({"T": "success", "msg": "authenticated"},),
        (
            {
                "T": "subscription",
                "trades": [],
                "quotes": [],
                "bars": list(symbols),
                "updatedBars": list(symbols),
                "dailyBars": [],
            },
        ),
    ]


def _bar_frame(
    *,
    message_type: str = "b",
    ticker: str = "TSLA",
    start: datetime = datetime(2026, 2, 2, 15, 30, tzinfo=UTC),
    close: float = 97.0,
    volume: int = 2000,
) -> dict[str, object]:
    return {
        "T": message_type,
        "S": ticker,
        "o": close,
        "h": close,
        "l": close,
        "c": close,
        "v": volume,
        "t": start.isoformat().replace("+00:00", "Z"),
    }


def _provider(
    transport: FakeStockStreamTransport,
    *,
    symbols: tuple[str, ...] = WATCHLIST,
    secret: str = SECRET,
) -> AlpacaMarketData:
    return AlpacaMarketData(
        http=ScriptedHistoryHttp(),
        clock=FixedClock(RETRIEVED_AT),
        feed="iex",
        sleeper=lambda _seconds: None,
        transport=transport,
        key_id="test-key-id",
        secret=secret,
        symbols=symbols,
    )


def _minute(start_at: datetime, close: Decimal) -> MarketBar:
    return MarketBar(
        ticker="TSLA",
        timeframe=MarketTimeframe.ONE_MINUTE,
        start_at=start_at,
        end_at=start_at + timedelta(minutes=1),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal("1000"),
        is_complete=True,
        provider="alpaca",
        feed="iex",
        retrieved_at=start_at + timedelta(minutes=1),
    )


def test_handshake_authenticates_and_subscribes_bars_and_updated_bars() -> None:
    transport = FakeStockStreamTransport(incoming=_handshake())
    provider = _provider(transport)

    provider.open_stock_stream()

    assert transport.connect_count == 1
    assert transport.authenticate_calls == [("test-key-id", SECRET)]
    assert transport.subscribe_calls == [WATCHLIST]
    assert transport.sent[0]["action"] == "auth"
    assert transport.sent[1] == {
        "action": "subscribe",
        "bars": ["TSLA", "SPY"],
        "updatedBars": ["TSLA", "SPY"],
    }
    assert "dailyBars" not in transport.sent[1]
    assert provider.holds_stock_stream is True
    assert provider.needs_stream_reconnect is False


def test_open_stock_stream_is_idempotent() -> None:
    transport = FakeStockStreamTransport(incoming=_handshake())
    provider = _provider(transport)

    provider.open_stock_stream()
    provider.open_stock_stream()

    assert transport.connect_count == 1
    assert transport.subscribe_calls == [WATCHLIST]


def test_completed_and_updated_frames_become_stream_minutes() -> None:
    transport = FakeStockStreamTransport(
        incoming=[
            *_handshake(),
            (_bar_frame(), _bar_frame(message_type="u", close=95.0)),
        ]
    )
    provider = _provider(transport)

    events = list(provider.iter_stream_minutes())

    assert [event.kind for event in events] == [
        StreamEventKind.BAR,
        StreamEventKind.UPDATED_BAR,
    ]
    assert events[0].bar.ticker == "TSLA"
    assert events[0].bar.close == Decimal("97.0")
    assert events[1].bar.close == Decimal("95.0")
    assert events[0].bar.bar_id == events[1].bar.bar_id
    assert events[0].bar.provider == "alpaca"
    assert events[0].bar.feed == "iex"
    assert events[0].bar.is_complete is True
    assert events[0].bar.retrieved_at == RETRIEVED_AT


def test_daily_bar_and_control_frames_are_not_mapped() -> None:
    transport = FakeStockStreamTransport(
        incoming=[
            *_handshake(),
            (
                {"T": "success", "msg": "authenticated"},
                _bar_frame(message_type="d"),
                {"T": "t", "S": "TSLA"},
                {"T": "q", "S": "TSLA"},
            ),
        ]
    )
    provider = _provider(transport)

    assert list(provider.iter_stream_minutes()) == []


def test_auth_failure_is_a_safe_diagnostic() -> None:
    transport = FakeStockStreamTransport(
        incoming=[
            ({"T": "success", "msg": "connected"},),
            ({"T": "error", "code": 402, "msg": f"auth failed {SECRET}"},),
        ]
    )
    provider = _provider(transport)

    with pytest.raises(StockStreamAuthError, match="authentication failed") as error:
        provider.open_stock_stream()

    assert SECRET not in str(error.value)
    assert provider.needs_stream_reconnect is False
    assert transport.connected is False


def test_resubscribe_closes_then_handshakes_again() -> None:
    transport = FakeStockStreamTransport(incoming=[*_handshake(), *_handshake()])
    provider = _provider(transport)
    provider.open_stock_stream()

    provider.resubscribe()

    assert transport.close_count == 1
    assert transport.connect_count == 2
    assert transport.subscribe_calls == [WATCHLIST, WATCHLIST]


def test_disconnect_marks_the_stream_for_reconnect() -> None:
    transport = FakeStockStreamTransport(incoming=[*_handshake()])
    transport.queue_disconnect()
    provider = _provider(transport)

    assert list(provider.iter_stream_minutes()) == []
    assert provider.needs_stream_reconnect is True


def test_qualifying_regular_session_frame_emits_through_ingest(
    tmp_path: Path,
) -> None:
    clock = SteppingClock(RETRIEVED_AT)
    transport = FakeStockStreamTransport(
        incoming=[
            *_handshake(),
            (
                _bar_frame(
                    start=SESSION_OPEN + timedelta(minutes=60),
                    close=97.0,
                    volume=2000,
                ),
            ),
        ]
    )
    provider = AlpacaMarketData(
        http=ScriptedHistoryHttp(),
        clock=clock,
        feed="iex",
        sleeper=lambda _seconds: None,
        transport=transport,
        key_id="test-key-id",
        secret=SECRET,
        symbols=WATCHLIST,
    )

    with SQLiteStorage(tmp_path / "stream-emit.sqlite3") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        for index in range(60):
            process_market_bar(
                storage=storage,
                manager=manager,
                bar=_minute(SESSION_OPEN + timedelta(minutes=index), Decimal("100")),
                watchlist=frozenset(WATCHLIST),
                now=SESSION_OPEN + timedelta(minutes=index + 1),
            )
        result = ingest_stream_minutes(
            storage=storage,
            manager=manager,
            provider=provider,
            watchlist=WATCHLIST,
            clock=clock,
        )
        processed = manager.process_pending(
            researcher=create_fake_research_report,
            notifier=lambda *_: None,
        )

    assert len(result.accepted_signal_ids) == 1
    assert len(processed) == 1
    assert result.persisted_bar_ids
