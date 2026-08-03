"""Application entry point."""

import logging
from datetime import UTC, datetime
from importlib.resources import as_file, files

from investment_assistant.clock import FixedClock
from investment_assistant.config import get_settings
from investment_assistant.logging_config import configure_logging
from investment_assistant.pipeline import run_pipeline

logger = logging.getLogger(__name__)

TRACKED_SYMBOL = "ACME"
SCENARIO_TIME = datetime(2026, 1, 15, 16, 30, tzinfo=UTC)


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
        "offline_walking_skeleton",
    )
    with as_file(fixture_resource) as fixture_directory:
        run_pipeline(
            database_path=settings.database_path,
            tracked_symbol=TRACKED_SYMBOL,
            market_fixture_path=fixture_directory / "triggering_market.json",
            news_fixture_path=fixture_directory / "triggering_news.json",
            clock=FixedClock(SCENARIO_TIME),
        )


if __name__ == "__main__":
    main()
