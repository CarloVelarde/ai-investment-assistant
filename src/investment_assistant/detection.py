"""Deterministic signal detection."""

from decimal import Decimal

from investment_assistant.models import MarketRecord, MarketSignal

MAXIMUM_PRICE_RATIO = Decimal("0.95")
MINIMUM_VOLUME_RATIO = Decimal("1.5")


def detect_market_signal(
    record: MarketRecord,
    *,
    tracked_symbol: str,
) -> MarketSignal | None:
    """Return a signal when the record passes the inclusive market rule."""

    normalized_tracked_symbol = tracked_symbol.strip().upper()
    if not normalized_tracked_symbol:
        raise ValueError("tracked symbol must not be blank")

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
