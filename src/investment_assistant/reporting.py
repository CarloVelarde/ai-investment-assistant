"""Fake research and console notification for the offline slice."""

import logging
from collections.abc import Callable

from investment_assistant.models import (
    Event,
    ResearchReport,
    Signal,
)

FAKE_RESEARCH_PREFIX = "FAKE RESEARCH — NOT INVESTMENT ANALYSIS"
EVENT_NOTIFICATION_PREFIX = "EVENT NOTIFICATION"

type Researcher = Callable[[Event, tuple[Signal, ...]], ResearchReport]
type Notifier = Callable[[Event, ResearchReport], None]

logger = logging.getLogger(__name__)


def create_fake_research_report(
    event: Event,
    signals: tuple[Signal, ...],
) -> ResearchReport:
    """Create fixed offline research for one durable event update."""

    return ResearchReport(
        report_id=f"report:{event.event_id}:{event.current_update}",
        event_id=event.event_id,
        event_update=event.current_update,
        ticker=event.ticker,
        event_occurred_at=event.created_at,
        created_at=event.updated_at,
        summary=(
            f"{FAKE_RESEARCH_PREFIX}: {event.ticker} event update "
            f"{event.current_update} includes {len(signals)} qualifying signal(s)."
        ),
        is_fake=True,
    )


def emit_console_notification(
    event: Event,
    report: ResearchReport,
) -> None:
    """Emit one marked console notification for an event and report."""

    logger.info(
        "%s | ticker=%s | event_id=%s | update=%s | report=%s",
        EVENT_NOTIFICATION_PREFIX,
        event.ticker,
        event.event_id,
        event.current_update,
        report.summary,
    )
