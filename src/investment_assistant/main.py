"""Application entry point."""

import logging

from investment_assistant.config import get_settings
from investment_assistant.logging_config import configure_logging

logger = logging.getLogger(__name__)


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


if __name__ == "__main__":
    main()
