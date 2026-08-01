"""Deterministic signal detection."""

from decimal import Decimal

from investment_assistant.models import (
    MarketRecord,
    MarketSignal,
    NewsRecord,
    NewsSignal,
)

MAXIMUM_PRICE_RATIO = Decimal("0.95")
MINIMUM_VOLUME_RATIO = Decimal("1.5")
NEGATIVE_EVENT_PHRASES = (
    "guidance cut",
    "earnings miss",
    "investigation",
    "product recall",
)


def detect_market_signal(
    record: MarketRecord,
    *,
    tracked_symbol: str,
) -> MarketSignal | None:
    """Return a signal when the record passes the inclusive market rule."""

    normalized_tracked_symbol = _normalize_tracked_symbol(tracked_symbol)

    if record.symbol != normalized_tracked_symbol:
        return None
    if record.latest_price > record.previous_close * MAXIMUM_PRICE_RATIO:
        return None

    volume_ratio = record.current_volume / record.average_volume
    if volume_ratio < MINIMUM_VOLUME_RATIO:
        return None

    return MarketSignal(
        symbol=record.symbol,
        occurred_at=record.occurred_at,
        price_decline_ratio=(record.previous_close - record.latest_price)
        / record.previous_close,
        volume_ratio=volume_ratio,
        provider=record.provider,
        feed=record.feed,
    )


def detect_news_signal(
    record: NewsRecord,
    *,
    tracked_symbol: str,
) -> NewsSignal | None:
    """Return a signal when the record contains a fixed negative phrase."""

    normalized_tracked_symbol = _normalize_tracked_symbol(tracked_symbol)
    if record.symbol != normalized_tracked_symbol:
        return None

    searchable_text = f"{record.headline}\n{record.summary}".casefold()
    matched_phrase = next(
        (phrase for phrase in NEGATIVE_EVENT_PHRASES if phrase in searchable_text),
        None,
    )
    if matched_phrase is None:
        return None

    return NewsSignal(
        symbol=record.symbol,
        occurred_at=record.published_at,
        matched_phrase=matched_phrase,
        headline=record.headline,
        source=record.source,
    )


def _normalize_tracked_symbol(symbol: str) -> str:
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("tracked symbol must not be blank")
    return normalized_symbol
