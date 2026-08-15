"""Application logging configuration"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from investment_assistant.ops_log import configure_watch_log

_JSON_EXTRA_FIELDS = (
    "environment",
    "live_mode",
    "session_open",
    "waiting_on_socket",
    "last_message_age_seconds",
    "last_spy_regular_end_at",
)


class JsonFormatter(logging.Formatter):
    """Format log records as JSON"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for name in _JSON_EXTRA_FIELDS:
            if hasattr(record, name):
                log_entry[name] = getattr(record, name)

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


def configure_logging(
    *,
    level: str,
    use_json: bool,
    watch_log: bool = False,
) -> None:
    """Configure application-wide console logging."""

    handler = logging.StreamHandler()

    if use_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        )

    logging.basicConfig(
        level=level,
        handlers=[handler],
        force=True,
    )
    configure_watch_log(watch_log)
