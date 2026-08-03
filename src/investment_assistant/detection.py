"""Deterministic signal detection."""

from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from investment_assistant.models import (
    MarketRecord,
    MarketSignal,
    MarketWindow,
    NewsRecord,
    NewsSignal,
    SignalDirection,
    SignalImportance,
    SourceDetails,
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
        signal_id=_stable_signal_id(
            "market",
            record.provider,
            record.feed,
            record.symbol,
            record.occurred_at.isoformat(),
        ),
        ticker=record.symbol,
        occurred_at=record.occurred_at,
        importance=SignalImportance.HIGH,
        source_details=SourceDetails(
            provider=record.provider,
            source=record.provider,
            feed=record.feed,
            retrieved_at=record.occurred_at,
        ),
        direction=SignalDirection.DOWN,
        rule="walking_skeleton_price_volume_decline",
        window=MarketWindow.ONE_HOUR,
        price_decline_ratio=(record.previous_close - record.latest_price)
        / record.previous_close,
        volume_ratio=volume_ratio,
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
        signal_id=_stable_signal_id(
            "news",
            record.source,
            record.symbol,
            record.published_at.isoformat(),
            record.headline,
        ),
        ticker=record.symbol,
        occurred_at=record.published_at,
        importance=SignalImportance.HIGH,
        source_details=SourceDetails(
            provider=record.source,
            source=record.source,
            feed=None,
            retrieved_at=record.published_at,
        ),
        category=matched_phrase.upper().replace(" ", "_"),
        direction=SignalDirection.DOWN,
        matched_phrase=matched_phrase,
        headline=record.headline,
    )


def _normalize_tracked_symbol(symbol: str) -> str:
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("tracked symbol must not be blank")
    return normalized_symbol


def _stable_signal_id(kind: str, *parts: str) -> str:
    identity = "|".join((kind, *parts))
    return f"{kind}-{uuid5(NAMESPACE_URL, identity)}"
