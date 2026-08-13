"""Application entry point."""

import logging
from datetime import UTC, datetime
from importlib.resources import as_file, files

from investment_assistant.clock import SteppingClock
from investment_assistant.config import get_settings
from investment_assistant.logging_config import configure_logging
from investment_assistant.pipeline import run_market_history

logger = logging.getLogger(__name__)

SCENARIO_TIME = datetime(2026, 2, 2, 14, 30, tzinfo=UTC)


def main() -> None:
    """Start the application."""

    settings = get_settings()

    configure_logging(
        level=settings.log_level,
        use_json=settings.log_json,
    )

    logger.info(
        "Application started",
        extra={"environment": settings.environment},
    )

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


if __name__ == "__main__":
    main()
