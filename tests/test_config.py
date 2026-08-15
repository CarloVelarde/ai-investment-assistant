"""Tests for application configuration."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from investment_assistant.config import (
    DEFAULT_ALPACA_TRADING_URL,
    MAX_LIVE_WATCHLIST_SYMBOLS,
    Settings,
    resolve_live_watchlist,
)

_SETTINGS_ENV = (
    "INVESTMENT_ASSISTANT_ENVIRONMENT",
    "INVESTMENT_ASSISTANT_LOG_LEVEL",
    "INVESTMENT_ASSISTANT_LOG_JSON",
    "INVESTMENT_ASSISTANT_DATABASE_PATH",
    "INVESTMENT_ASSISTANT_ALPACA_API_KEY_ID",
    "INVESTMENT_ASSISTANT_ALPACA_API_SECRET_KEY",
    "INVESTMENT_ASSISTANT_ALPACA_FEED",
    "INVESTMENT_ASSISTANT_ALPACA_TRADING_URL",
    "INVESTMENT_ASSISTANT_WATCHLIST",
)

_SECRET = "test-alpaca-secret-do-not-log"


def _clear_settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in _SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)


def _set_live_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVESTMENT_ASSISTANT_ALPACA_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("INVESTMENT_ASSISTANT_ALPACA_API_SECRET_KEY", _SECRET)


def test_default_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_env(monkeypatch, tmp_path)

    settings = Settings()

    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.log_json is True
    assert settings.database_path == Path("investment_assistant.db")
    assert settings.alpaca_api_key_id == ""
    assert settings.alpaca_api_secret_key.get_secret_value() == ""
    assert settings.alpaca_feed == "iex"
    assert settings.alpaca_trading_url == DEFAULT_ALPACA_TRADING_URL
    assert settings.watchlist == ""
    assert settings.live_mode is False


def test_database_path_can_be_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_env(monkeypatch, tmp_path)
    database_path = tmp_path / "state.sqlite3"
    monkeypatch.setenv(
        "INVESTMENT_ASSISTANT_DATABASE_PATH",
        str(database_path),
    )

    assert Settings().database_path == database_path


def test_missing_keys_keep_offline_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_env(monkeypatch, tmp_path)

    assert Settings().live_mode is False


def test_only_key_id_keeps_offline_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_env(monkeypatch, tmp_path)
    monkeypatch.setenv("INVESTMENT_ASSISTANT_ALPACA_API_KEY_ID", "test-key-id")

    assert Settings().live_mode is False


def test_only_secret_keeps_offline_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_env(monkeypatch, tmp_path)
    monkeypatch.setenv("INVESTMENT_ASSISTANT_ALPACA_API_SECRET_KEY", _SECRET)

    assert Settings().live_mode is False


def test_blank_keys_keep_offline_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_env(monkeypatch, tmp_path)
    monkeypatch.setenv("INVESTMENT_ASSISTANT_ALPACA_API_KEY_ID", "   ")
    monkeypatch.setenv("INVESTMENT_ASSISTANT_ALPACA_API_SECRET_KEY", "  ")

    assert Settings().live_mode is False


def test_both_keys_enable_live_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_env(monkeypatch, tmp_path)
    _set_live_keys(monkeypatch)
    monkeypatch.setenv("INVESTMENT_ASSISTANT_WATCHLIST", "TSLA,AMD")

    settings = Settings()

    assert settings.live_mode is True
    assert settings.alpaca_api_key_id == "test-key-id"
    assert settings.alpaca_api_secret_key.get_secret_value() == _SECRET
    assert settings.alpaca_feed == "iex"
    assert settings.alpaca_trading_url == DEFAULT_ALPACA_TRADING_URL


def test_live_settings_load_feed_and_trading_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_env(monkeypatch, tmp_path)
    _set_live_keys(monkeypatch)
    monkeypatch.setenv("INVESTMENT_ASSISTANT_WATCHLIST", "TSLA")
    monkeypatch.setenv("INVESTMENT_ASSISTANT_ALPACA_FEED", "SIP")
    monkeypatch.setenv(
        "INVESTMENT_ASSISTANT_ALPACA_TRADING_URL",
        "https://api.alpaca.markets",
    )

    settings = Settings()

    assert settings.alpaca_feed == "sip"
    assert settings.alpaca_trading_url == "https://api.alpaca.markets"


def test_invalid_feed_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_env(monkeypatch, tmp_path)
    monkeypatch.setenv("INVESTMENT_ASSISTANT_ALPACA_FEED", "polygon")

    with pytest.raises(ValidationError):
        Settings()


def test_secret_is_not_exposed_in_repr_or_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_env(monkeypatch, tmp_path)
    _set_live_keys(monkeypatch)
    monkeypatch.setenv("INVESTMENT_ASSISTANT_WATCHLIST", "TSLA")

    settings = Settings()
    rendered = f"{settings!r} {settings!s} {settings.model_dump(mode='json')}"

    assert _SECRET not in rendered
    assert _SECRET not in str(settings.alpaca_api_secret_key)
    assert settings.alpaca_api_secret_key.get_secret_value() == _SECRET


def test_watchlist_normalizes_tickers_and_includes_spy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_env(monkeypatch, tmp_path)
    _set_live_keys(monkeypatch)
    monkeypatch.setenv("INVESTMENT_ASSISTANT_WATCHLIST", " tsla , amd,tsla ")

    assert Settings().watched_tickers() == ("TSLA", "AMD", "SPY")


def test_watchlist_does_not_duplicate_spy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_env(monkeypatch, tmp_path)
    _set_live_keys(monkeypatch)
    monkeypatch.setenv("INVESTMENT_ASSISTANT_WATCHLIST", "SPY,TSLA")

    assert Settings().watched_tickers() == ("SPY", "TSLA")


def test_blank_watchlist_is_rejected_in_live_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_env(monkeypatch, tmp_path)
    _set_live_keys(monkeypatch)

    with pytest.raises(ValidationError, match="watchlist must not be blank"):
        Settings()


def test_blank_watchlist_is_allowed_when_offline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_env(monkeypatch, tmp_path)

    settings = Settings()

    assert settings.live_mode is False
    assert settings.watchlist == ""
    with pytest.raises(ValueError, match="watchlist must not be blank"):
        settings.watched_tickers()


def test_thirty_symbols_including_spy_are_accepted() -> None:
    symbols = [f"T{index:02d}" for index in range(MAX_LIVE_WATCHLIST_SYMBOLS - 1)]
    symbols.append("SPY")

    assert resolve_live_watchlist(",".join(symbols)) == tuple(symbols)


def test_thirty_symbols_without_spy_are_rejected() -> None:
    symbols = [f"T{index:02d}" for index in range(MAX_LIVE_WATCHLIST_SYMBOLS)]

    with pytest.raises(ValueError, match="may not exceed 30 symbols"):
        resolve_live_watchlist(",".join(symbols))


def test_more_than_thirty_symbols_including_spy_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_settings_env(monkeypatch, tmp_path)
    _set_live_keys(monkeypatch)
    symbols = [f"T{index:02d}" for index in range(MAX_LIVE_WATCHLIST_SYMBOLS)]
    symbols.append("SPY")
    monkeypatch.setenv("INVESTMENT_ASSISTANT_WATCHLIST", ",".join(symbols))

    with pytest.raises(ValidationError, match="may not exceed 30 symbols"):
        Settings()
