"""Application configuration"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_LIVE_WATCHLIST_SYMBOLS = 30
COMPARISON_TICKER = "SPY"
DEFAULT_ALPACA_TRADING_URL = "https://paper-api.alpaca.markets"


def parse_watchlist(raw: str) -> tuple[str, ...]:
    """Split a comma-separated watchlist into unique uppercase tickers."""

    tickers: list[str] = []
    seen: set[str] = set()
    for token in raw.split(","):
        ticker = token.strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        tickers.append(ticker)
    return tuple(tickers)


def resolve_live_watchlist(raw: str) -> tuple[str, ...]:
    """Return the live watchlist, always including SPY.

    A blank list is rejected. More than 30 symbols including SPY is rejected.
    """

    tickers = list(parse_watchlist(raw))
    if not tickers:
        raise ValueError("watchlist must not be blank")
    if COMPARISON_TICKER not in tickers:
        tickers.append(COMPARISON_TICKER)
    if len(tickers) > MAX_LIVE_WATCHLIST_SYMBOLS:
        raise ValueError(
            f"watchlist plus SPY may not exceed {MAX_LIVE_WATCHLIST_SYMBOLS} symbols"
        )
    return tuple(tickers)


class Settings(BaseSettings):
    """Configuration loaded from environment variables"""

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = True
    heartbeat: bool = False
    watch_log: bool = False
    database_path: Path = Path("investment_assistant.db")
    alpaca_api_key_id: str = ""
    alpaca_api_secret_key: SecretStr = SecretStr("")
    alpaca_feed: Literal["iex", "sip"] = "iex"
    alpaca_trading_url: str = DEFAULT_ALPACA_TRADING_URL
    openai_api_key: SecretStr = SecretStr("")
    watchlist: str = ""

    model_config = SettingsConfigDict(
        env_prefix="INVESTMENT_ASSISTANT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("alpaca_api_key_id", "watchlist", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("alpaca_api_secret_key", "openai_api_key", mode="before")
    @classmethod
    def _strip_secret(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, SecretStr):
            return SecretStr(value.get_secret_value().strip())
        return value

    @field_validator("alpaca_feed", mode="before")
    @classmethod
    def _normalize_feed(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("alpaca_trading_url", mode="before")
    @classmethod
    def _normalize_trading_url(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or DEFAULT_ALPACA_TRADING_URL
        return value

    @model_validator(mode="after")
    def _reject_invalid_live_watchlist(self) -> Self:
        if self.live_mode:
            resolve_live_watchlist(self.watchlist)
        return self

    @property
    def live_mode(self) -> bool:
        """Return True when both Alpaca keys are present and non-blank."""

        return bool(self.alpaca_api_key_id) and bool(
            self.alpaca_api_secret_key.get_secret_value()
        )

    def watched_tickers(self) -> tuple[str, ...]:
        """Return the normalized live watchlist including SPY."""

        return resolve_live_watchlist(self.watchlist)

    def news_watchlist(self) -> tuple[str, ...]:
        """Return the explicit user watchlist used for news classification.

        Comparison-only SPY is not added. SPY is included only when the user
        configured it.
        """

        tickers = parse_watchlist(self.watchlist)
        if not tickers:
            raise ValueError("watchlist must not be blank")
        return tickers


@lru_cache
def get_settings() -> Settings:
    """Load and cache application settings."""

    return Settings()
