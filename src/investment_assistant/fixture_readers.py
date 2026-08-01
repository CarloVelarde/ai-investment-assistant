"""Load and normalize offline walking-skeleton fixtures."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from investment_assistant.models import MarketRecord, NewsRecord

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
