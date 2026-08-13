"""Load and normalize offline walking-skeleton fixtures."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from investment_assistant.models import (
    MarketBar,
    MarketRecord,
    MarketTimeframe,
    NewsRecord,
)

NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PositiveDecimal = Annotated[Decimal, Field(gt=0)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]


class _MarketFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: NonBlankText
    latest_price: PositiveDecimal
    previous_close: PositiveDecimal
    current_volume: NonNegativeDecimal
    average_volume: PositiveDecimal
    observed_at: AwareDatetime
    provider: NonBlankText
    feed: NonBlankText


class _NewsFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: NonBlankText
    headline: NonBlankText
    summary: str
    published_at: AwareDatetime
    source: NonBlankText


class _MarketBarFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: NonBlankText
    timeframe: Literal["1Min", "1Day"]
    start_at: AwareDatetime
    end_at: AwareDatetime
    open: PositiveDecimal
    high: PositiveDecimal
    low: PositiveDecimal
    close: PositiveDecimal
    volume: NonNegativeDecimal
    is_complete: bool
    provider: NonBlankText
    feed: NonBlankText
    retrieved_at: AwareDatetime


class _MarketHistoryFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: NonBlankText
    watchlist: tuple[NonBlankText, ...]
    bars: tuple[_MarketBarFixture, ...]


def load_market_fixture(path: Path) -> MarketRecord:
    """Load one market fixture into a normalized internal record."""

    fixture = _MarketFixture.model_validate_json(path.read_text(encoding="utf-8"))
    return MarketRecord(
        symbol=_normalize_symbol(fixture.ticker),
        latest_price=fixture.latest_price,
        previous_close=fixture.previous_close,
        current_volume=fixture.current_volume,
        average_volume=fixture.average_volume,
        occurred_at=_to_utc(fixture.observed_at),
        provider=fixture.provider,
        feed=fixture.feed,
    )


def load_market_history_fixture(
    path: Path,
) -> tuple[str, tuple[str, ...], tuple[MarketBar, ...]]:
    """Load a bar-history scenario into normalized market bars."""

    fixture = _MarketHistoryFixture.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    watchlist = tuple(_normalize_symbol(ticker) for ticker in fixture.watchlist)
    bars = tuple(
        MarketBar(
            ticker=_normalize_symbol(item.ticker),
            timeframe=MarketTimeframe(item.timeframe),
            start_at=_to_utc(item.start_at),
            end_at=_to_utc(item.end_at),
            open=item.open,
            high=item.high,
            low=item.low,
            close=item.close,
            volume=item.volume,
            is_complete=item.is_complete,
            provider=item.provider,
            feed=item.feed,
            retrieved_at=_to_utc(item.retrieved_at),
        )
        for item in fixture.bars
    )
    return fixture.scenario, watchlist, bars


def load_news_fixture(path: Path) -> NewsRecord:
    """Load one news fixture into a normalized internal record."""

    fixture = _NewsFixture.model_validate_json(path.read_text(encoding="utf-8"))
    return NewsRecord(
        symbol=_normalize_symbol(fixture.ticker),
        headline=fixture.headline,
        summary=fixture.summary,
        published_at=_to_utc(fixture.published_at),
        source=fixture.source,
    )


def _normalize_symbol(symbol: str) -> str:
    return symbol.upper()


def _to_utc(timestamp: datetime) -> datetime:
    return timestamp.astimezone(UTC)
