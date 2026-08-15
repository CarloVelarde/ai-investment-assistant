"""Startup backfill, quiet replay, and live minute ingest."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from investment_assistant.clock import Clock
from investment_assistant.event_manager import EventManager
from investment_assistant.market_data import (
    AlpacaFeed,
    MarketData,
    StreamMinute,
    daily_backfill_start,
    fetch_all_history,
    is_regular_session_minute,
    live_cutoff,
    minute_backfill_range,
    stream_minute_from_alpaca,
)
from investment_assistant.models import MarketBar, MarketTimeframe
from investment_assistant.pipeline import MarketBarProcessingResult, process_market_bar
from investment_assistant.storage import SQLiteStorage


@dataclass(frozen=True, slots=True)
class LiveIngestResult:
    """Observable outcome of backfill or stream ingest."""

    accepted_signal_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    closed_event_ids: tuple[str, ...] = ()
    persisted_bar_ids: tuple[str, ...] = ()


def backfill_and_replay(
    *,
    storage: SQLiteStorage,
    manager: EventManager,
    provider: MarketData,
    watchlist: Sequence[str],
    clock: Clock,
) -> LiveIngestResult:
    """Fetch history, persist it, and quiet-replay bars older than today's open."""

    now = clock.now()
    symbols = tuple(ticker.strip().upper() for ticker in watchlist if ticker.strip())
    cutoff = live_cutoff(now)
    daily_start = daily_backfill_start(now)
    minute_start, minute_end = minute_backfill_range(now)
    fetched = (
        *fetch_all_history(
            provider,
            symbols=symbols,
            timeframe=MarketTimeframe.ONE_DAY,
            start=daily_start,
            end=now,
        ),
        *fetch_all_history(
            provider,
            symbols=symbols,
            timeframe=MarketTimeframe.ONE_MINUTE,
            start=minute_start,
            end=minute_end,
        ),
    )
    return _replay_bars(
        storage=storage,
        manager=manager,
        bars=fetched,
        watchlist=frozenset(symbols),
        clock=clock,
        cutoff=cutoff,
    )


def ingest_stream_minute(
    *,
    storage: SQLiteStorage,
    manager: EventManager,
    event: StreamMinute,
    watchlist: Sequence[str],
    now: datetime,
) -> MarketBarProcessingResult:
    """Persist a completed stream minute and evaluate regular-session bars."""

    bar = event.bar
    watched = frozenset(
        ticker.strip().upper() for ticker in watchlist if ticker.strip()
    )
    if not is_regular_session_minute(bar.start_at):
        storage.save_market_bar(bar)
        return MarketBarProcessingResult(
            diagnostics=(f"stored extended-hours minute {bar.bar_id}",),
        )
    return process_market_bar(
        storage=storage,
        manager=manager,
        bar=bar,
        watchlist=watched,
        now=now,
    )


def ingest_stream_payload(
    *,
    storage: SQLiteStorage,
    manager: EventManager,
    payload: Mapping[str, object],
    feed: AlpacaFeed,
    retrieved_at: datetime,
    watchlist: Sequence[str],
    now: datetime,
) -> MarketBarProcessingResult:
    """Map one Alpaca stream message and ingest completed minutes only."""

    if payload.get("T") == "d":
        return MarketBarProcessingResult(
            diagnostics=("ignored running daily bar",),
        )
    if feed not in ("iex", "sip"):
        raise ValueError("feed must be iex or sip")
    event = stream_minute_from_alpaca(
        payload,
        feed=feed,
        retrieved_at=retrieved_at,
    )
    return ingest_stream_minute(
        storage=storage,
        manager=manager,
        event=event,
        watchlist=watchlist,
        now=now,
    )


def ingest_stream_minutes(
    *,
    storage: SQLiteStorage,
    manager: EventManager,
    provider: MarketData,
    watchlist: Sequence[str],
    clock: Clock,
) -> LiveIngestResult:
    """Ingest every queued stream minute from the market-data port."""

    accepted: list[str] = []
    diagnostics: list[str] = []
    closed: list[str] = []
    persisted: list[str] = []
    watched = tuple(watchlist)
    for event in provider.iter_stream_minutes():
        _sync_clock(clock, event.bar.end_at)
        outcome = ingest_stream_minute(
            storage=storage,
            manager=manager,
            event=event,
            watchlist=watched,
            now=clock.now(),
        )
        accepted.extend(outcome.accepted_signal_ids)
        diagnostics.extend(outcome.diagnostics)
        closed.extend(outcome.closed_event_ids)
        persisted.append(event.bar.bar_id)
    return LiveIngestResult(
        accepted_signal_ids=tuple(accepted),
        diagnostics=tuple(diagnostics),
        closed_event_ids=tuple(closed),
        persisted_bar_ids=tuple(persisted),
    )


def _replay_bars(
    *,
    storage: SQLiteStorage,
    manager: EventManager,
    bars: tuple[MarketBar, ...],
    watchlist: frozenset[str],
    clock: Clock,
    cutoff: datetime,
) -> LiveIngestResult:
    accepted: list[str] = []
    diagnostics: list[str] = []
    closed: list[str] = []
    persisted: list[str] = []
    for bar in _bars_in_evaluation_order(bars):
        _sync_clock(clock, bar.end_at)
        outcome = process_market_bar(
            storage=storage,
            manager=manager,
            bar=bar,
            watchlist=watchlist,
            now=clock.now(),
            emit_signals=bar.end_at >= cutoff,
        )
        persisted.append(bar.bar_id)
        accepted.extend(outcome.accepted_signal_ids)
        diagnostics.extend(outcome.diagnostics)
        closed.extend(outcome.closed_event_ids)
    return LiveIngestResult(
        accepted_signal_ids=tuple(accepted),
        diagnostics=tuple(diagnostics),
        closed_event_ids=tuple(closed),
        persisted_bar_ids=tuple(persisted),
    )


def _bars_in_evaluation_order(bars: tuple[MarketBar, ...]) -> tuple[MarketBar, ...]:
    return tuple(
        sorted(
            bars,
            key=lambda bar: (
                bar.end_at,
                bar.start_at,
                0 if bar.ticker == "SPY" else 1,
                bar.ticker,
                bar.bar_id,
            ),
        )
    )


def _sync_clock(clock: Clock, when: datetime) -> None:
    advance_to = getattr(clock, "advance_to", None)
    if callable(advance_to):
        advance_to(when)
