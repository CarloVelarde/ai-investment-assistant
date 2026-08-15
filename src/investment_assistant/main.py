"""Application entry point."""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.resources import as_file, files

from investment_assistant.alpaca import (
    AlpacaMarketData,
    UrllibHistoryHttp,
    fetch_alpaca_session,
)
from investment_assistant.clock import Clock, SteppingClock, SystemClock
from investment_assistant.config import Settings, get_settings
from investment_assistant.event_manager import EventManager
from investment_assistant.live_ingest import (
    LiveIngestResult,
    StreamHealth,
    backfill_and_replay,
    diagnose_stream_health,
    ingest_stream_minutes,
    reconnect_stream,
    recover_stale_stream,
    run_after_close_daily,
)
from investment_assistant.logging_config import configure_logging
from investment_assistant.market_data import (
    MarketData,
    MarketDataError,
    last_closed_session_date,
    live_cutoff,
)
from investment_assistant.ops_log import (
    emit_heartbeat,
    heartbeat_is_due,
    watch,
)
from investment_assistant.pipeline import run_market_history
from investment_assistant.reporting import (
    Notifier,
    Researcher,
    create_fake_research_report,
    emit_console_notification,
)
from investment_assistant.stock_stream import (
    StockStreamAuthError,
    WebsocketStockStreamTransport,
    stock_stream_url,
)
from investment_assistant.storage import SQLiteStorage

logger = logging.getLogger(__name__)

SCENARIO_TIME = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)
LIVE_POLL_SECONDS = 30


def main(
    *,
    settings: Settings | None = None,
    provider: MarketData | None = None,
    clock: Clock | None = None,
    loop: bool = True,
    max_cycles: int | None = None,
    sleeper: Callable[[float], None] | None = None,
    researcher: Researcher = create_fake_research_report,
    notifier: Notifier = emit_console_notification,
) -> LiveIngestResult | None:
    """Start the application.

    Missing Alpaca keys keep the offline abrupt-drop fixture path. Live keys
    run backfill, then one stock websocket, after-close daily, and stale
    recovery in one process. Tests inject a fake provider and set
    ``loop=False``.
    """

    settings = settings or get_settings()
    configure_logging(
        level=settings.log_level,
        use_json=settings.log_json,
        watch_log=settings.watch_log,
    )
    logger.info(
        "Application started",
        extra={
            "environment": settings.environment,
            "live_mode": settings.live_mode,
        },
    )
    watch(
        "Application started",
        live_mode=settings.live_mode,
        feed=settings.alpaca_feed,
        database_path=str(settings.database_path),
        watchlist=(",".join(settings.watched_tickers()) if settings.live_mode else ""),
    )
    if not settings.live_mode:
        watch("Offline fixture path")
        run_offline_console(settings)
        return None
    live_clock = clock or SystemClock()
    live_provider = provider or build_live_provider(settings, live_clock)
    poll = time.sleep if sleeper is None else sleeper
    try:
        return run_live_session(
            settings,
            provider=live_provider,
            clock=live_clock,
            loop=loop,
            max_cycles=max_cycles,
            sleeper=poll,
            researcher=researcher,
            notifier=notifier,
        )
    except KeyboardInterrupt:
        logger.info("Stopped")
        closer = getattr(live_provider, "close_stock_stream", None)
        if callable(closer):
            closer()
        return None


def run_offline_console(settings: Settings) -> None:
    """Replay the packaged abrupt-drop fixture through the offline pipeline."""

    fixture_resource = files("investment_assistant").joinpath(
        "fixtures",
        "market_history",
        "abrupt_drop.json",
    )
    with as_file(fixture_resource) as fixture_path:
        run_market_history(
            database_path=settings.database_path,
            fixture_path=fixture_path,
            clock=SteppingClock(SCENARIO_TIME),
        )


def run_live_session(
    settings: Settings,
    *,
    provider: MarketData,
    clock: Clock,
    loop: bool,
    sleeper: Callable[[float], None],
    researcher: Researcher,
    notifier: Notifier,
    max_cycles: int | None = None,
) -> LiveIngestResult:
    """Run startup backfill, then one or more live cycles."""

    watchlist = settings.watched_tickers()
    health = StreamHealth(started_at=clock.now())
    latest = LiveIngestResult()
    last_daily_date = None
    last_heartbeat_at: datetime | None = None
    cycles = 0
    with SQLiteStorage(settings.database_path) as storage:
        storage.initialize()
        manager = EventManager(storage, clock=clock)
        latest = backfill_and_replay(
            storage=storage,
            manager=manager,
            provider=provider,
            watchlist=watchlist,
            clock=clock,
        )
        watch(
            "Backfill complete",
            bars=len(latest.persisted_bar_ids),
            accepted=len(latest.accepted_signal_ids),
            cutoff=live_cutoff(clock.now()).isoformat(),
        )
        _open_stock_stream(provider)
        watch(
            "Stock stream ready",
            url=getattr(provider, "stock_stream_url", None),
            symbols=",".join(watchlist),
            channels="bars,updatedBars",
        )
        heartbeat_started_at = clock.now()
        while True:
            stream = ingest_stream_minutes(
                storage=storage,
                manager=manager,
                provider=provider,
                watchlist=watchlist,
                clock=clock,
                health=health,
            )
            latest = _combine(latest, stream)
            if stream.persisted_bar_ids:
                watch(
                    "Stream minutes ingested",
                    minutes=len(stream.persisted_bar_ids),
                    accepted=len(stream.accepted_signal_ids),
                )
            session = provider.get_session()
            if not session.is_open:
                session_day = last_closed_session_date(clock.now())
                if last_daily_date != session_day:
                    daily = run_after_close_daily(
                        storage=storage,
                        manager=manager,
                        provider=provider,
                        watchlist=watchlist,
                        clock=clock,
                    )
                    latest = _combine(latest, daily)
                    last_daily_date = session_day
                    watch(
                        "After-close daily scan",
                        session_day=session_day.isoformat(),
                        bars=len(daily.persisted_bar_ids),
                        accepted=len(daily.accepted_signal_ids),
                    )
            reconnected = _reconnect_if_dropped(
                provider,
                storage=storage,
                manager=manager,
                watchlist=watchlist,
                clock=clock,
                sleeper=sleeper,
            )
            if reconnected is not None:
                latest = _combine(latest, reconnected)
                watch(
                    "Stock stream reconnected",
                    bars=len(reconnected.persisted_bar_ids),
                )
            stale = diagnose_stream_health(
                health,
                now=clock.now(),
                session=session,
            )
            if stale.is_stale and reconnected is None:
                recovered = recover_stale_stream(
                    storage=storage,
                    manager=manager,
                    provider=provider,
                    watchlist=watchlist,
                    clock=clock,
                    health=health,
                    sleeper=sleeper,
                )
                latest = _combine(latest, recovered)
                watch(
                    "Stale stream recovered",
                    diagnostics=",".join(stale.diagnostics),
                    bars=len(recovered.persisted_bar_ids),
                )
            processed = manager.process_pending(
                researcher=researcher,
                notifier=notifier,
            )
            if processed:
                watch(
                    "Pending events processed",
                    events=len(processed),
                    tickers=",".join(event.ticker for event in processed),
                )
            now = clock.now()
            if settings.heartbeat and heartbeat_is_due(
                now=now,
                started_at=heartbeat_started_at,
                last_emitted_at=last_heartbeat_at,
            ):
                emit_heartbeat(
                    logger,
                    now=now,
                    session_open=session.is_open,
                    last_message_at=health.last_message_at,
                    last_spy_regular_end_at=health.last_spy_regular_end_at,
                    waiting_on_socket=True,
                )
                last_heartbeat_at = now
            cycles += 1
            if not loop:
                break
            if max_cycles is not None and cycles >= max_cycles:
                break
            if not getattr(provider, "holds_stock_stream", False):
                sleeper(LIVE_POLL_SECONDS)
    return latest


def build_live_provider(settings: Settings, clock: Clock) -> AlpacaMarketData:
    """Build the production Alpaca adapter. Tests inject a fake instead."""

    secret = settings.alpaca_api_secret_key.get_secret_value()
    key_id = settings.alpaca_api_key_id
    trading_url = settings.alpaca_trading_url
    feed = settings.alpaca_feed
    return AlpacaMarketData(
        http=UrllibHistoryHttp(key_id=key_id, secret=secret),
        clock=clock,
        feed=feed,
        sleeper=time.sleep,
        session_provider=lambda: fetch_alpaca_session(
            trading_url,
            key_id=key_id,
            secret=secret,
        ),
        transport=WebsocketStockStreamTransport(stock_stream_url(feed)),
        key_id=key_id,
        secret=secret,
        symbols=settings.watched_tickers(),
    )


def _open_stock_stream(provider: MarketData) -> None:
    opener = getattr(provider, "open_stock_stream", None)
    if not callable(opener):
        return
    try:
        opener()
    except StockStreamAuthError:
        raise
    except MarketDataError:
        logger.warning("Stock stream not open after backfill; will retry")


def _reconnect_if_dropped(
    provider: MarketData,
    *,
    storage: SQLiteStorage,
    manager: EventManager,
    watchlist: tuple[str, ...],
    clock: Clock,
    sleeper: Callable[[float], None],
) -> LiveIngestResult | None:
    if not getattr(provider, "needs_stream_reconnect", False):
        return None
    return reconnect_stream(
        storage=storage,
        manager=manager,
        provider=provider,
        watchlist=watchlist,
        clock=clock,
        sleeper=sleeper,
    )


def _combine(left: LiveIngestResult, right: LiveIngestResult) -> LiveIngestResult:
    return LiveIngestResult(
        accepted_signal_ids=left.accepted_signal_ids + right.accepted_signal_ids,
        diagnostics=left.diagnostics + right.diagnostics,
        closed_event_ids=left.closed_event_ids + right.closed_event_ids,
        persisted_bar_ids=left.persisted_bar_ids + right.persisted_bar_ids,
    )


if __name__ == "__main__":
    main()
