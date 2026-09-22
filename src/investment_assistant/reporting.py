"""Fake research and console notification for reports."""

import logging
from collections.abc import Callable

from investment_assistant.models import (
    Event,
    ResearchReport,
    Signal,
)
from investment_assistant.ops_log import watch

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
    """Emit one console notification for an event and report.

    Live reports include posture, uncertainty, and a compact source list.
    Fake reports keep their explicit label in the summary.
    """

    parts = [
        EVENT_NOTIFICATION_PREFIX,
        f"ticker={event.ticker}",
        f"event_id={event.event_id}",
        f"update={event.current_update}",
    ]
    details = report.details
    if details is not None:
        sources = ",".join(source.reference for source in details.sources)
        parts.extend(
            [
                f"posture={details.analysis.posture}",
                f"uncertainty={details.analysis.uncertainty}",
                f"sources={sources}",
            ]
        )
    parts.append(f"report={report.summary}")
    logger.info(" | ".join(parts))
    watch(
        "Event notification",
        ticker=event.ticker,
        event_id=event.event_id,
        update=event.current_update,
        posture=None if details is None else details.analysis.posture,
        fake=report.is_fake,
    )
