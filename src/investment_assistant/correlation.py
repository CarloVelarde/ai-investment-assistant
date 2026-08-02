"""Deterministic signal correlation."""

from datetime import timedelta

from investment_assistant.clock import Clock
from investment_assistant.models import MarketSignal, NewsSignal, SignificantEvent

CORRELATION_WINDOW = timedelta(minutes=60)


def correlate_signals(
    market_signal: MarketSignal | None,
    news_signal: NewsSignal | None,
    *,
    clock: Clock,
) -> SignificantEvent | None:
    """Create one event when both signals match by symbol and time."""

    if market_signal is None or news_signal is None:
        return None
    if market_signal.symbol != news_signal.symbol:
        return None

    time_difference = abs(market_signal.occurred_at - news_signal.occurred_at)
    if time_difference > CORRELATION_WINDOW:
        return None

    return SignificantEvent(
        symbol=market_signal.symbol,
        occurred_at=clock.now(),
        market_signal=market_signal,
        news_signal=news_signal,
    )
