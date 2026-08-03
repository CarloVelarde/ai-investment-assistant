"""Fake research and console notification for the offline slice."""

import logging
from collections.abc import Callable

from investment_assistant.models import (
    Event,
    ResearchReport,
    Signal,
    SignificantEvent,
)

FAKE_RESEARCH_PREFIX = "FAKE RESEARCH — NOT INVESTMENT ANALYSIS"
EVENT_NOTIFICATION_PREFIX = "EVENT NOTIFICATION"

type Researcher = Callable[[SignificantEvent], ResearchReport]
type Notifier = Callable[[SignificantEvent, ResearchReport], None]

logger = logging.getLogger(__name__)


def create_fake_research_report(event: SignificantEvent) -> ResearchReport:
    """Create fixed, clearly labeled research output for an event."""

    event_id = f"walking-skeleton:{event.symbol}:{event.occurred_at.isoformat()}"
    return ResearchReport(
        report_id=f"report:{event_id}:1",
        event_id=event_id,
        event_update=1,
        ticker=event.symbol,
        event_occurred_at=event.occurred_at,
        created_at=event.occurred_at,
        summary=(
            f"{FAKE_RESEARCH_PREFIX}: {event.symbol} has correlated market and news "
            f"signals matching '{event.news_signal.matched_phrase}'."
        ),
        is_fake=True,
    )


def create_fake_durable_research_report(
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
    event: SignificantEvent,
    report: ResearchReport,
) -> None:
    """Emit one marked console notification for an event and report."""

    logger.info(
        "%s | symbol=%s | event_time=%s | report=%s",
        EVENT_NOTIFICATION_PREFIX,
        event.symbol,
        event.occurred_at.isoformat(),
        report.summary,
    )


def research_and_notify(
    event: SignificantEvent | None,
    *,
    researcher: Researcher = create_fake_research_report,
    notifier: Notifier = emit_console_notification,
) -> ResearchReport | None:
    """Research and notify only when correlation produced an event."""

    if event is None:
        return None

    report = researcher(event)
    notifier(event, report)
    return report
