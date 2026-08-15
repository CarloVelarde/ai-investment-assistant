"""Opt-in heartbeat and watch-log helpers. Defaults stay silent."""

import logging
from datetime import UTC, datetime, timedelta

WATCH_LOGGER_NAME = "investment_assistant.watch"
HEARTBEAT_INTERVAL = timedelta(seconds=60)
HEARTBEAT_MESSAGE = "Still watching"

_watch_enabled = False


def configure_watch_log(enabled: bool) -> None:
    """Enable or disable the separate plain-text watch logger."""

    global _watch_enabled
    _watch_enabled = enabled
    watch_logger = logging.getLogger(WATCH_LOGGER_NAME)
    watch_logger.handlers.clear()
    watch_logger.propagate = False
    if not enabled:
        watch_logger.addHandler(logging.NullHandler())
        watch_logger.setLevel(logging.CRITICAL + 1)
        watch_logger.disabled = True
        return
    handler = logging.StreamHandler()
    handler.setFormatter(WatchFormatter())
    watch_logger.addHandler(handler)
    watch_logger.setLevel(logging.INFO)
    watch_logger.disabled = False


def watch(message: str, **fields: object) -> None:
    """Emit one watch-log line when that logger is on. No-op otherwise."""

    if not _watch_enabled:
        return
    try:
        logging.getLogger(WATCH_LOGGER_NAME).info(
            message,
            extra={"watch": _safe_fields(fields)},
        )
    except Exception:
        return


def heartbeat_is_due(
    *,
    now: datetime,
    started_at: datetime,
    last_emitted_at: datetime | None,
) -> bool:
    """Return True when a heartbeat should fire at ``now``."""

    reference = last_emitted_at if last_emitted_at is not None else started_at
    return now - reference >= HEARTBEAT_INTERVAL


def emit_heartbeat(
    logger: logging.Logger,
    *,
    now: datetime,
    session_open: bool,
    last_message_at: datetime | None,
    last_spy_regular_end_at: datetime | None,
    waiting_on_socket: bool,
) -> None:
    """Log one regular-logger heartbeat. Callers must already know it is due."""

    age_seconds = (
        None if last_message_at is None else (now - last_message_at).total_seconds()
    )
    try:
        logger.info(
            HEARTBEAT_MESSAGE,
            extra={
                "session_open": session_open,
                "waiting_on_socket": waiting_on_socket,
                "last_message_age_seconds": age_seconds,
                "last_spy_regular_end_at": (
                    None
                    if last_spy_regular_end_at is None
                    else last_spy_regular_end_at.isoformat()
                ),
            },
        )
    except Exception:
        return


class WatchFormatter(logging.Formatter):
    """Plain-text watch lines with key=value extras."""

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created, tz=UTC)
        fields = getattr(record, "watch", {})
        extra = ""
        if isinstance(fields, dict) and fields:
            extra = " | " + " ".join(f"{key}={value}" for key, value in fields.items())
        return f"{stamp.isoformat()} | WATCH | {record.getMessage()}{extra}"


def _safe_fields(fields: dict[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in fields.items():
        if _blocked_field_name(key):
            continue
        safe[key] = value
    return safe


def _blocked_field_name(key: str) -> bool:
    lowered = key.lower()
    return (
        lowered in {"secret", "password", "token", "authorization"}
        or "secret" in lowered
        or "key_id" in lowered
        or lowered.endswith("_key")
    )
