"""Startup backfill, quiet replay, and live minute ingest."""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from investment_assistant.clock import Clock
from investment_assistant.event_manager import EventManager
from investment_assistant.market_data import (
    EASTERN,
    AlpacaFeed,
    MarketData,
    MarketSession,
    StreamMinute,
    daily_backfill_start,
    fetch_all_history,
    is_regular_session_minute,
    last_closed_session_date,
    live_cutoff,
    minute_backfill_range,
    regular_session_close,
    regular_session_open,
    stream_minute_from_alpaca,
    with_daily_completeness,
)
from investment_assistant.market_detection import detect_session_gap_from_storage
from investment_assistant.models import MarketBar, MarketTimeframe
from investment_assistant.ops_log import watch
from investment_assistant.pipeline import MarketBarProcessingResult, process_market_bar
from investment_assistant.storage import SQLiteStorage

logger = logging.getLogger(__name__)

STREAM_SILENCE = timedelta(seconds=120)
SPY_STALE = timedelta(minutes=5)
MAX_RECONNECT_BACKOFF = 32.0


@dataclass(frozen=True, slots=True)
class LiveIngestResult:
    """Observable outcome of backfill or stream ingest."""

    accepted_signal_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    closed_event_ids: tuple[str, ...] = ()
    persisted_bar_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class StreamHealth:
    """In-memory last-seen times for stale-stream checks."""

    started_at: datetime
    last_message_at: datetime | None = None
    last_spy_regular_end_at: datetime | None = None

    def record(self, event: StreamMinute, *, now: datetime) -> None:
        """Remember a websocket minute for silence and SPY freshness."""

        self.last_message_at = now
        if event.bar.ticker == "SPY" and is_regular_session_minute(event.bar.start_at):
            self.last_spy_regular_end_at = event.bar.end_at


@dataclass(frozen=True, slots=True)
class StaleStreamStatus:
    """Whether the live stock stream looks silent or SPY-stale."""

    diagnostics: tuple[str, ...]
    socket_silent: bool
    spy_stale: bool

    @property
    def is_stale(self) -> bool:
        """Return True when a reconnect or gap fill should run."""

        return self.socket_silent or self.spy_stale


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
        watch(
            "Stream minute stored",
            ticker=bar.ticker,
            kind=event.kind.value,
            start_at=bar.start_at.isoformat(),
            evaluated=False,
        )
        return MarketBarProcessingResult(
            diagnostics=(f"stored extended-hours minute {bar.bar_id}",),
        )
    result = process_market_bar(
        storage=storage,
        manager=manager,
        bar=bar,
        watchlist=watched,
        now=now,
    )
    if _is_first_regular_minute(storage, bar, session_at=now):
        gap = _process_session_gap(
            storage=storage,
            manager=manager,
            ticker=bar.ticker,
            session_at=now,
            now=now,
        )
        result = _combine_processing_results(result, gap)
    watch(
        "Stream minute evaluated",
        ticker=bar.ticker,
        kind=event.kind.value,
        start_at=bar.start_at.isoformat(),
        evaluated=True,
        accepted=len(result.accepted_signal_ids),
        closed=len(result.closed_event_ids),
    )
    return result


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
        watch("Ignored running daily bar", ticker=str(payload.get("S", "")))
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
    health: StreamHealth | None = None,
) -> LiveIngestResult:
    """Ingest every queued stream minute from the market-data port."""

    accepted: list[str] = []
    diagnostics: list[str] = []
    closed: list[str] = []
    persisted: list[str] = []
    watched = tuple(watchlist)
    for event in provider.iter_stream_minutes():
        _sync_clock(clock, event.bar.end_at)
        if health is not None:
            health.record(event, now=clock.now())
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


def run_after_close_daily(
    *,
    storage: SQLiteStorage,
    manager: EventManager,
    provider: MarketData,
    watchlist: Sequence[str],
    clock: Clock,
) -> LiveIngestResult:
    """Fetch completed REST ``1Day`` bars after regular close and evaluate them."""

    session = provider.get_session()
    if session.is_open:
        return LiveIngestResult(diagnostics=("regular session still open",))
    now = clock.now()
    symbols = _watched_symbols(watchlist)
    session_day = last_closed_session_date(now)
    start = datetime.combine(session_day, time.min, tzinfo=EASTERN).astimezone(UTC)
    bars = fetch_all_history(
        provider,
        symbols=symbols,
        timeframe=MarketTimeframe.ONE_DAY,
        start=start,
        end=now,
    )
    logger.info(
        "After-close daily fetch",
        extra={"session_day": session_day.isoformat(), "bars": len(bars)},
    )
    watch(
        "After-close daily fetch",
        session_day=session_day.isoformat(),
        bars=len(bars),
    )
    return _replay_bars(
        storage=storage,
        manager=manager,
        bars=bars,
        watchlist=frozenset(symbols),
        clock=clock,
        cutoff=live_cutoff(now),
    )


def fill_minute_gap(
    *,
    storage: SQLiteStorage,
    manager: EventManager,
    provider: MarketData,
    watchlist: Sequence[str],
    clock: Clock,
) -> LiveIngestResult:
    """REST-fill missing regular-session minutes from last persisted bar to now."""

    now = clock.now()
    symbols = _watched_symbols(watchlist)
    start = _minute_gap_start(storage, symbols, now)
    bars = fetch_all_history(
        provider,
        symbols=symbols,
        timeframe=MarketTimeframe.ONE_MINUTE,
        start=start,
        end=now,
    )
    logger.info(
        "Minute gap fill",
        extra={"start": start.isoformat(), "bars": len(bars)},
    )
    return _replay_bars(
        storage=storage,
        manager=manager,
        bars=bars,
        watchlist=frozenset(symbols),
        clock=clock,
        cutoff=live_cutoff(now),
    )


def reconnect_stream(
    *,
    storage: SQLiteStorage,
    manager: EventManager,
    provider: MarketData,
    watchlist: Sequence[str],
    clock: Clock,
    sleeper: Callable[[float], None],
    attempt: int = 0,
) -> LiveIngestResult:
    """Back off, resubscribe, then REST-fill the disconnect gap."""

    delay = min(2.0**attempt, MAX_RECONNECT_BACKOFF)
    logger.warning(
        "Stock stream reconnecting",
        extra={"attempt": attempt + 1, "delay_seconds": delay},
    )
    watch("Stock stream reconnecting", attempt=attempt + 1, delay_seconds=delay)
    sleeper(delay)
    _resubscribe(provider)
    gap = fill_minute_gap(
        storage=storage,
        manager=manager,
        provider=provider,
        watchlist=watchlist,
        clock=clock,
    )
    return LiveIngestResult(
        accepted_signal_ids=gap.accepted_signal_ids,
        diagnostics=("reconnected stock stream", *gap.diagnostics),
        closed_event_ids=gap.closed_event_ids,
        persisted_bar_ids=gap.persisted_bar_ids,
    )


def diagnose_stream_health(
    health: StreamHealth,
    *,
    now: datetime,
    session: MarketSession,
) -> StaleStreamStatus:
    """Detect socket silence or a stale SPY minute during regular hours."""

    if not session.is_open or not _during_regular_hours(now):
        return StaleStreamStatus((), False, False)
    socket_silent = (
        now - (health.last_message_at or health.started_at)
    ) >= STREAM_SILENCE
    spy_stale = (
        now - (health.last_spy_regular_end_at or health.started_at)
    ) >= SPY_STALE
    diagnostics: list[str] = []
    if socket_silent:
        diagnostics.append("stale stream: no websocket data for 120 seconds")
    if spy_stale:
        diagnostics.append(
            "stale stream: SPY has no new regular-session minute for 5 minutes"
        )
    return StaleStreamStatus(tuple(diagnostics), socket_silent, spy_stale)


def recover_stale_stream(
    *,
    storage: SQLiteStorage,
    manager: EventManager,
    provider: MarketData,
    watchlist: Sequence[str],
    clock: Clock,
    health: StreamHealth,
    sleeper: Callable[[float], None],
) -> LiveIngestResult:
    """Backfill a stale gap; reconnect only when the socket itself looks dead."""

    status = diagnose_stream_health(
        health,
        now=clock.now(),
        session=provider.get_session(),
    )
    if not status.is_stale:
        return LiveIngestResult()
    logger.warning(
        "Stale stock stream",
        extra={"diagnostics": list(status.diagnostics)},
    )
    watch("Stale stock stream", diagnostics=",".join(status.diagnostics))
    if status.socket_silent:
        return reconnect_stream(
            storage=storage,
            manager=manager,
            provider=provider,
            watchlist=watchlist,
            clock=clock,
            sleeper=sleeper,
        )
    gap = fill_minute_gap(
        storage=storage,
        manager=manager,
        provider=provider,
        watchlist=watchlist,
        clock=clock,
    )
    return LiveIngestResult(
        accepted_signal_ids=gap.accepted_signal_ids,
        diagnostics=(*status.diagnostics, *gap.diagnostics),
        closed_event_ids=gap.closed_event_ids,
        persisted_bar_ids=gap.persisted_bar_ids,
    )


def _watched_symbols(watchlist: Sequence[str]) -> tuple[str, ...]:
    return tuple(ticker.strip().upper() for ticker in watchlist if ticker.strip())


def _during_regular_hours(now: datetime) -> bool:
    return regular_session_open(now) <= now <= regular_session_close(now)


def _resubscribe(provider: MarketData) -> None:
    resubscribe = getattr(provider, "resubscribe", None)
    if callable(resubscribe):
        resubscribe()


def _minute_gap_start(
    storage: SQLiteStorage,
    symbols: Sequence[str],
    now: datetime,
) -> datetime:
    ends: list[datetime] = []
    for symbol in symbols:
        bars = storage.list_market_bars(
            symbol,
            MarketTimeframe.ONE_MINUTE,
            complete_only=True,
            limit=1,
        )
        if bars:
            ends.append(bars[-1].end_at)
    if ends:
        return min(ends)
    session_start, _ = minute_backfill_range(now)
    return session_start


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
    as_of = clock.now()
    for bar in _bars_in_evaluation_order(bars):
        bar = with_daily_completeness(bar, as_of=as_of)
        _sync_clock(clock, bar.end_at)
        outcome = process_market_bar(
            storage=storage,
            manager=manager,
            bar=bar,
            watchlist=watchlist,
            now=clock.now(),
            emit_signals=_replay_may_emit(bar, cutoff=cutoff, as_of=as_of),
        )
        persisted.append(bar.bar_id)
        accepted.extend(outcome.accepted_signal_ids)
        diagnostics.extend(outcome.diagnostics)
        closed.extend(outcome.closed_event_ids)
    for ticker in sorted(watchlist):
        gap = _process_session_gap(
            storage=storage,
            manager=manager,
            ticker=ticker,
            session_at=as_of,
            now=as_of,
        )
        accepted.extend(gap.accepted_signal_ids)
        diagnostics.extend(gap.diagnostics)
        closed.extend(gap.closed_event_ids)
    return LiveIngestResult(
        accepted_signal_ids=tuple(accepted),
        diagnostics=tuple(diagnostics),
        closed_event_ids=tuple(closed),
        persisted_bar_ids=tuple(persisted),
    )


def _replay_may_emit(
    bar: MarketBar,
    *,
    cutoff: datetime,
    as_of: datetime,
) -> bool:
    """Return True when a replayed bar may send signals to the event manager."""

    return bar.is_complete and cutoff <= bar.end_at <= as_of


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


def _process_session_gap(
    *,
    storage: SQLiteStorage,
    manager: EventManager,
    ticker: str,
    session_at: datetime,
    now: datetime,
) -> MarketBarProcessingResult:
    accepted: list[str] = []
    with storage.transaction():
        result = detect_session_gap_from_storage(
            storage,
            ticker,
            session_at=session_at,
            now=now,
        )
        for signal in result.signals:
            if manager.handle_signal(signal).accepted:
                accepted.append(signal.signal_id)
    return MarketBarProcessingResult(
        accepted_signal_ids=tuple(accepted),
        diagnostics=result.diagnostics,
    )


def _is_first_regular_minute(
    storage: SQLiteStorage,
    bar: MarketBar,
    *,
    session_at: datetime,
) -> bool:
    session_start = regular_session_open(session_at)
    session_end = regular_session_close(session_at)
    minutes = storage.list_market_bars(
        bar.ticker,
        MarketTimeframe.ONE_MINUTE,
        complete_only=True,
        start_at_or_after=session_start,
        through_start_at=session_end,
    )
    first = next(
        (item for item in minutes if is_regular_session_minute(item.start_at)),
        None,
    )
    return first is not None and first.bar_id == bar.bar_id


def _combine_processing_results(
    first: MarketBarProcessingResult,
    second: MarketBarProcessingResult,
) -> MarketBarProcessingResult:
    return MarketBarProcessingResult(
        accepted_signal_ids=(
            *first.accepted_signal_ids,
            *second.accepted_signal_ids,
        ),
        diagnostics=(*first.diagnostics, *second.diagnostics),
        closed_event_ids=(*first.closed_event_ids, *second.closed_event_ids),
    )
