"""Tests for offline vs live application startup. No network."""

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from investment_assistant.alpaca import AlpacaMarketData, HistoryHttpResponse
from investment_assistant.clock import SteppingClock
from investment_assistant.config import Settings
from investment_assistant.main import build_live_provider, main
from investment_assistant.market_data import (
    FakeMarketData,
    MarketSession,
    StreamMinute,
)
from investment_assistant.models import MarketBar, MarketTimeframe
from investment_assistant.stock_stream import (
    STOCK_STREAM_PREFIX,
    FakeStockStreamTransport,
    StockStreamAuthError,
    WebsocketStockStreamTransport,
    stock_stream_url,
)
from investment_assistant.storage import SQLiteStorage

SECRET = "test-alpaca-secret-do-not-log"
CLOSED_AT = datetime(2026, 2, 2, 21, 5, tzinfo=UTC)
CLOSED_SESSION = MarketSession(
    is_open=False,
    timestamp=CLOSED_AT,
    next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
    next_close=datetime(2026, 2, 3, 21, 0, tzinfo=UTC),
)
OPEN_AT = datetime(2026, 2, 2, 15, 31, tzinfo=UTC)
OPEN_SESSION = MarketSession(
    is_open=True,
    timestamp=OPEN_AT,
    next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
    next_close=datetime(2026, 2, 2, 21, 0, tzinfo=UTC),
)


def _daily() -> MarketBar:
    start_at = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)
    return MarketBar(
        ticker="TSLA",
        timeframe=MarketTimeframe.ONE_DAY,
        start_at=start_at,
        end_at=start_at + timedelta(hours=6, minutes=30),
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=Decimal("1000000"),
        is_complete=True,
        provider="alpaca",
        feed="iex",
        retrieved_at=CLOSED_AT,
    )


def test_main_stays_on_the_offline_fixture_path_without_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "INVESTMENT_ASSISTANT_ALPACA_API_KEY_ID",
        "INVESTMENT_ASSISTANT_ALPACA_API_SECRET_KEY",
        "INVESTMENT_ASSISTANT_WATCHLIST",
    ):
        monkeypatch.delenv(name, raising=False)
    calls: list[Path] = []

    def fake_history(**kwargs: Any) -> None:
        calls.append(kwargs["fixture_path"])

    monkeypatch.setattr("investment_assistant.main.run_market_history", fake_history)
    settings = Settings(database_path=tmp_path / "offline.sqlite3")

    result = main(settings=settings, loop=False)

    assert result is None
    assert len(calls) == 1
    assert calls[0].name == "abrupt_drop.json"


def test_main_stops_quietly_on_keyboard_interrupt(tmp_path: Path) -> None:
    class InterruptingProvider(FakeMarketData):
        def iter_stream_minutes(self) -> Iterator[StreamMinute]:
            raise KeyboardInterrupt
            yield from ()

    settings = Settings(
        alpaca_api_key_id="test-key-id",
        alpaca_api_secret_key=SecretStr(SECRET),
        watchlist="TSLA",
        database_path=tmp_path / "interrupt.sqlite3",
    )
    provider = InterruptingProvider(history=(_daily(),), session=OPEN_SESSION)

    result = main(
        settings=settings,
        provider=provider,
        clock=SteppingClock(OPEN_AT),
        loop=True,
        sleeper=lambda _seconds: None,
        notifier=lambda *_: None,
    )

    assert result is None


def test_main_runs_a_live_cycle_when_keys_are_present(tmp_path: Path) -> None:
    database_path = tmp_path / "live.sqlite3"
    settings = Settings(
        alpaca_api_key_id="test-key-id",
        alpaca_api_secret_key=SecretStr(SECRET),
        watchlist="TSLA",
        database_path=database_path,
    )
    bar = _daily()
    provider = FakeMarketData(history=(bar,), session=CLOSED_SESSION)

    result = main(
        settings=settings,
        provider=provider,
        clock=SteppingClock(CLOSED_AT),
        loop=False,
        sleeper=lambda _seconds: None,
        notifier=lambda *_: None,
    )

    assert settings.live_mode is True
    assert result is not None
    assert bar.bar_id in result.persisted_bar_ids
    with SQLiteStorage(database_path) as storage:
        assert storage.get_market_bar(bar.bar_id) == bar


class ScriptedHistoryHttp:
    def __init__(self) -> None:
        self.requests = 0

    def get_stock_bars(self, params: Mapping[str, str]) -> HistoryHttpResponse:
        del params
        self.requests += 1
        return HistoryHttpResponse(status_code=200, body={"bars": {}})


def _handshake_frames() -> list[tuple[dict[str, object], ...]]:
    return [
        ({"T": "success", "msg": "connected"},),
        ({"T": "success", "msg": "authenticated"},),
        (
            {
                "T": "subscription",
                "bars": ["TSLA", "SPY"],
                "updatedBars": ["TSLA", "SPY"],
                "dailyBars": [],
            },
        ),
    ]


def _live_settings(database_path: Path) -> Settings:
    return Settings(
        alpaca_api_key_id="test-key-id",
        alpaca_api_secret_key=SecretStr(SECRET),
        watchlist="TSLA",
        database_path=database_path,
    )


def test_build_live_provider_opens_one_stock_feed_url() -> None:
    settings = _live_settings(Path("unused.sqlite3"))
    provider = build_live_provider(settings, SteppingClock(CLOSED_AT))

    assert isinstance(provider._transport, WebsocketStockStreamTransport)
    assert provider.stock_stream_url == f"{STOCK_STREAM_PREFIX}iex"
    assert provider.stock_stream_url == stock_stream_url("iex")
    assert provider.holds_stock_stream is True
    assert provider.stream_symbols == ("TSLA", "SPY")


def test_main_opens_one_stock_stream_after_backfill(tmp_path: Path) -> None:
    transport = FakeStockStreamTransport(
        incoming=[
            *_handshake_frames(),
            (
                {
                    "T": "b",
                    "S": "TSLA",
                    "o": 100,
                    "h": 100,
                    "l": 100,
                    "c": 100,
                    "v": 1000,
                    "t": "2026-02-02T15:30:00Z",
                },
                {
                    "T": "d",
                    "S": "TSLA",
                    "o": 100,
                    "h": 100,
                    "l": 100,
                    "c": 100,
                    "v": 1000,
                    "t": "2026-02-02T14:30:00Z",
                },
            ),
        ]
    )
    provider = AlpacaMarketData(
        http=ScriptedHistoryHttp(),
        clock=SteppingClock(datetime(2026, 2, 2, 15, 31, tzinfo=UTC)),
        feed="iex",
        sleeper=lambda _seconds: None,
        session=MarketSession(
            is_open=True,
            timestamp=datetime(2026, 2, 2, 15, 31, tzinfo=UTC),
            next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
            next_close=datetime(2026, 2, 2, 21, 0, tzinfo=UTC),
        ),
        transport=transport,
        key_id="test-key-id",
        secret=SECRET,
        symbols=("TSLA", "SPY"),
    )

    result = main(
        settings=_live_settings(tmp_path / "socket.sqlite3"),
        provider=provider,
        clock=SteppingClock(datetime(2026, 2, 2, 15, 31, tzinfo=UTC)),
        loop=False,
        sleeper=lambda _seconds: None,
        notifier=lambda *_: None,
    )

    assert result is not None
    assert transport.connect_count == 1
    assert transport.subscribe_calls == [("TSLA", "SPY")]
    assert transport.sent[1]["action"] == "subscribe"
    assert "dailyBars" not in transport.sent[1]
    assert any(
        bar_id.endswith(":TSLA:1Min:2026-02-02T15:30:00+00:00")
        for bar_id in result.persisted_bar_ids
    )


def test_main_auth_failure_is_safe_and_does_not_print_the_secret(
    tmp_path: Path,
) -> None:
    transport = FakeStockStreamTransport(
        incoming=[
            ({"T": "success", "msg": "connected"},),
            ({"T": "error", "code": 402, "msg": "auth failed"},),
        ]
    )
    provider = AlpacaMarketData(
        http=ScriptedHistoryHttp(),
        clock=SteppingClock(OPEN_AT),
        feed="iex",
        sleeper=lambda _seconds: None,
        session=OPEN_SESSION,
        transport=transport,
        key_id="test-key-id",
        secret=SECRET,
        symbols=("TSLA", "SPY"),
    )

    with pytest.raises(StockStreamAuthError, match="authentication failed") as error:
        main(
            settings=_live_settings(tmp_path / "auth.sqlite3"),
            provider=provider,
            clock=SteppingClock(OPEN_AT),
            loop=False,
            sleeper=lambda _seconds: None,
            notifier=lambda *_: None,
        )

    assert SECRET not in str(error.value)
    assert transport.connect_count == 1


def test_main_opens_only_during_session_and_closes_at_session_end(
    tmp_path: Path,
) -> None:
    preopen = MarketSession(
        is_open=False,
        timestamp=datetime(2026, 2, 3, 13, 0, tzinfo=UTC),
        next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
        next_close=datetime(2026, 2, 3, 21, 0, tzinfo=UTC),
    )
    open_session = MarketSession(
        is_open=True,
        timestamp=datetime(2026, 2, 3, 14, 31, tzinfo=UTC),
        next_open=datetime(2026, 2, 4, 14, 30, tzinfo=UTC),
        next_close=datetime(2026, 2, 3, 21, 0, tzinfo=UTC),
    )
    postclose = MarketSession(
        is_open=False,
        timestamp=datetime(2026, 2, 3, 21, 1, tzinfo=UTC),
        next_open=datetime(2026, 2, 4, 14, 30, tzinfo=UTC),
        next_close=datetime(2026, 2, 4, 21, 0, tzinfo=UTC),
    )
    clock = SteppingClock(preopen.timestamp)
    sessions = iter((preopen, preopen, open_session, postclose, postclose))

    def next_session() -> MarketSession:
        session = next(sessions)
        clock.advance_to(session.timestamp)
        return session

    transport = FakeStockStreamTransport(incoming=_handshake_frames())
    provider = AlpacaMarketData(
        http=ScriptedHistoryHttp(),
        clock=clock,
        feed="iex",
        sleeper=lambda _seconds: None,
        session_provider=next_session,
        transport=transport,
        key_id="test-key-id",
        secret=SECRET,
        symbols=("TSLA", "SPY"),
    )
    sleeps: list[float] = []

    result = main(
        settings=_live_settings(tmp_path / "session-transition.sqlite3"),
        provider=provider,
        clock=clock,
        loop=True,
        max_cycles=3,
        sleeper=sleeps.append,
        notifier=lambda *_: None,
    )

    assert result is not None
    assert transport.connect_count == 1
    assert transport.subscribe_calls == [("TSLA", "SPY")]
    assert transport.close_count == 1
    assert provider.stock_stream_is_open is False
    assert provider.needs_stream_reconnect is False
    assert sleeps == [30]


def test_main_reconnects_when_the_stock_stream_drops(tmp_path: Path) -> None:
    transport = FakeStockStreamTransport(incoming=_handshake_frames())
    transport.queue_disconnect()
    transport.push_frames(*_handshake_frames())
    provider = AlpacaMarketData(
        http=ScriptedHistoryHttp(),
        clock=SteppingClock(datetime(2026, 2, 2, 15, 31, tzinfo=UTC)),
        feed="iex",
        sleeper=lambda _seconds: None,
        session=MarketSession(
            is_open=True,
            timestamp=datetime(2026, 2, 2, 15, 31, tzinfo=UTC),
            next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
            next_close=datetime(2026, 2, 2, 21, 0, tzinfo=UTC),
        ),
        transport=transport,
        key_id="test-key-id",
        secret=SECRET,
        symbols=("TSLA", "SPY"),
    )

    result = main(
        settings=_live_settings(tmp_path / "reconnect.sqlite3"),
        provider=provider,
        clock=SteppingClock(datetime(2026, 2, 2, 15, 31, tzinfo=UTC)),
        loop=False,
        sleeper=lambda _seconds: None,
        notifier=lambda *_: None,
    )

    assert result is not None
    assert "reconnected stock stream" in result.diagnostics
    assert transport.connect_count == 2
    assert transport.subscribe_calls == [("TSLA", "SPY"), ("TSLA", "SPY")]


def test_env_example_documents_live_settings_without_secrets() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")

    assert "INVESTMENT_ASSISTANT_ALPACA_API_KEY_ID=" in text
    assert "INVESTMENT_ASSISTANT_ALPACA_API_SECRET_KEY=" in text
    assert "INVESTMENT_ASSISTANT_ALPACA_FEED=iex" in text
    assert "INVESTMENT_ASSISTANT_WATCHLIST=" in text
    assert "INVESTMENT_ASSISTANT_HEARTBEAT=false" in text
    assert "INVESTMENT_ASSISTANT_WATCH_LOG=false" in text
    assert SECRET not in text
    assert "sk-" not in text
