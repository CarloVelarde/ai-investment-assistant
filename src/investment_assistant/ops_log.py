"""Opt-in heartbeat and watch-log helpers. Defaults stay silent."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from investment_assistant.config import Settings
from investment_assistant.model_budget import CLASSIFIER_ALLOWANCE, RESEARCH_ALLOWANCE
from investment_assistant.storage import SQLiteStorage

WATCH_LOGGER_NAME = "investment_assistant.watch"
HEARTBEAT_INTERVAL = timedelta(seconds=60)
HEARTBEAT_MESSAGE = "Still watching"

_watch_enabled = False


@dataclass(frozen=True, slots=True)
class OperationalStatus:
    """A safe snapshot of current delivery and model admission state."""

    discord_configured: bool
    notification_destination: str
    pending_notifications: int
    uncertain_deliveries: int
    uncertain_awaiting_resend: int
    failed_deliveries: int
    oldest_pending_age_seconds: float | None
    model_estimated_charged_usd: float
    model_estimated_reserved_usd: float
    model_estimated_remaining_usd: float
    research_admission: str
    classifier_admission: str
    research_required_usd: float
    classifier_required_usd: float


def operational_status(
    storage: SQLiteStorage, settings: Settings, now: datetime
) -> OperationalStatus:
    """Read current work and UTC-day ledger without changing durable state."""

    day = now.astimezone(UTC).date().isoformat()
    charged, reserved = storage.model_budget_totals(day)
    remaining = max(0, settings.model_budget_microdollars - charged - reserved)
    pending, uncertain, awaiting_resend, failed, oldest_age = (
        storage.notification_backlog(now)
    )
    common_reason = None
    if not settings.openai_api_key.get_secret_value():
        common_reason = "MISSING_KEY"
    elif storage.model_budget_migration_hold_on(now):
        common_reason = "MIGRATION_HOLD"
    elif storage.model_budget_overrun(day):
        common_reason = "ESTIMATION_OVERRUN"
    research_reason = common_reason
    classifier_reason = common_reason
    if research_reason is None:
        if storage.research_starts_on(now) >= settings.research_starts_per_day:
            research_reason = "DAILY_BUDGET"
        elif remaining < RESEARCH_ALLOWANCE:
            research_reason = "MODEL_BUDGET"
    if classifier_reason is None:
        if storage.classifier_call_count(day) >= settings.classifier_calls_per_day:
            classifier_reason = "DAILY_BUDGET"
        elif settings.classifier_calls_per_pass == 0:
            classifier_reason = "PASS_BUDGET"
        elif remaining < CLASSIFIER_ALLOWANCE:
            classifier_reason = "MODEL_BUDGET"
    configured = bool(settings.discord_webhook_url.get_secret_value())
    return OperationalStatus(
        discord_configured=configured,
        notification_destination="discord" if configured else "console",
        pending_notifications=pending,
        uncertain_deliveries=uncertain,
        uncertain_awaiting_resend=awaiting_resend,
        failed_deliveries=failed,
        oldest_pending_age_seconds=oldest_age,
        model_estimated_charged_usd=charged / 1_000_000,
        model_estimated_reserved_usd=reserved / 1_000_000,
        model_estimated_remaining_usd=remaining / 1_000_000,
        research_admission=research_reason or "AVAILABLE",
        classifier_admission=classifier_reason or "AVAILABLE",
        research_required_usd=RESEARCH_ALLOWANCE / 1_000_000,
        classifier_required_usd=CLASSIFIER_ALLOWANCE / 1_000_000,
    )


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
    status: OperationalStatus | None = None,
) -> None:
    """Log one regular-logger heartbeat. Callers must already know it is due."""

    age_seconds = (
        None if last_message_at is None else (now - last_message_at).total_seconds()
    )
    try:
        fields: dict[str, object] = {
            "session_open": session_open,
            "waiting_on_socket": waiting_on_socket,
            "last_message_age_seconds": age_seconds,
            "last_spy_regular_end_at": (
                None
                if last_spy_regular_end_at is None
                else last_spy_regular_end_at.isoformat()
            ),
        }
        if status is not None:
            fields.update(
                (name, getattr(status, name)) for name in status.__dataclass_fields__
            )
        logger.info(
            HEARTBEAT_MESSAGE,
            extra=fields,
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
