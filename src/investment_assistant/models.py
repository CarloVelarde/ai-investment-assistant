"""Normalized records used by core application logic."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class MarketRecord:
    """Normalized market data from one observation."""

    symbol: str
    latest_price: Decimal
    previous_close: Decimal
    current_volume: Decimal
    average_volume: Decimal
    occurred_at: datetime
    provider: str
    feed: str


@dataclass(frozen=True, slots=True)
class NewsRecord:
    """Normalized news data from one article."""

    symbol: str
    headline: str
    summary: str
    published_at: datetime
    source: str


@dataclass(frozen=True, slots=True)
class MarketSignal:
    """A market record that passed the walking-skeleton rule."""

    symbol: str
    occurred_at: datetime
    price_decline_ratio: Decimal
    volume_ratio: Decimal
    provider: str
    feed: str


@dataclass(frozen=True, slots=True)
class NewsSignal:
    """A news record that passed the walking-skeleton filter."""

    symbol: str
    occurred_at: datetime
    matched_phrase: str
    headline: str
    source: str


@dataclass(frozen=True, slots=True)
class SignificantEvent:
    """One correlated market and news event."""

    symbol: str
    occurred_at: datetime
    market_signal: MarketSignal
    news_signal: NewsSignal


@dataclass(frozen=True, slots=True)
class ResearchReport:
    """Structured research output for one event."""

    symbol: str
    event_occurred_at: datetime
    summary: str
    is_fake: bool
