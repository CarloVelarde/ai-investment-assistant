"""Tests for offline vs live application startup. No network."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from investment_assistant.clock import SteppingClock
from investment_assistant.config import Settings
from investment_assistant.main import main
from investment_assistant.market_data import FakeMarketData, MarketSession
from investment_assistant.models import MarketBar, MarketTimeframe
from investment_assistant.storage import SQLiteStorage

SECRET = "test-alpaca-secret-do-not-log"
CLOSED_AT = datetime(2026, 2, 2, 21, 5, tzinfo=UTC)
CLOSED_SESSION = MarketSession(
    is_open=False,
    timestamp=CLOSED_AT,
    next_open=datetime(2026, 2, 3, 14, 30, tzinfo=UTC),
    next_close=datetime(2026, 2, 3, 21, 0, tzinfo=UTC),
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


def test_env_example_documents_live_settings_without_secrets() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")

    assert "INVESTMENT_ASSISTANT_ALPACA_API_KEY_ID=" in text
    assert "INVESTMENT_ASSISTANT_ALPACA_API_SECRET_KEY=" in text
    assert "INVESTMENT_ASSISTANT_ALPACA_FEED=iex" in text
    assert "INVESTMENT_ASSISTANT_WATCHLIST=" in text
    assert SECRET not in text
    assert "sk-" not in text
