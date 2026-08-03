"""Tests for fake research and console notification."""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from investment_assistant.models import (
    MarketSignal,
    MarketWindow,
    NewsSignal,
    ResearchReport,
    SignalDirection,
    SignalImportance,
    SignificantEvent,
    SourceDetails,
)
from investment_assistant.reporting import (
    EVENT_NOTIFICATION_PREFIX,
    FAKE_RESEARCH_PREFIX,
    create_fake_research_report,
    research_and_notify,
)

MARKET_TIME = datetime(2026, 1, 15, 15, 30, tzinfo=UTC)
EVENT_TIME = datetime(2026, 1, 15, 16, 30, tzinfo=UTC)
MARKET_SIGNAL = MarketSignal(
    signal_id="market-1",
    ticker="ACME",
    occurred_at=MARKET_TIME,
    importance=SignalImportance.HIGH,
    source_details=SourceDetails(
        provider="fixture",
        source="fixture",
        feed="offline-demo",
        retrieved_at=MARKET_TIME,
    ),
    direction=SignalDirection.DOWN,
    rule="price-volume-decline",
    window=MarketWindow.ONE_HOUR,
    price_decline_ratio=Decimal("0.06"),
    volume_ratio=Decimal("1.6"),
)
NEWS_SIGNAL = NewsSignal(
    signal_id="news-1",
    ticker="ACME",
    occurred_at=MARKET_TIME + timedelta(minutes=15),
    importance=SignalImportance.HIGH,
    source_details=SourceDetails(
        provider="fixture",
        source="Fixture Wire",
        feed="offline-demo",
        retrieved_at=MARKET_TIME + timedelta(minutes=15),
    ),
    category="GUIDANCE",
    direction=SignalDirection.DOWN,
    matched_phrase="guidance cut",
    headline="Acme announces guidance cut",
)
EVENT = SignificantEvent(
    symbol="ACME",
    occurred_at=EVENT_TIME,
    market_signal=MARKET_SIGNAL,
    news_signal=NEWS_SIGNAL,
)


def test_creates_labeled_fake_research_report() -> None:
    report = create_fake_research_report(EVENT)

    assert report.symbol == "ACME"
    assert report.event_occurred_at == EVENT_TIME
    assert report.summary.startswith(FAKE_RESEARCH_PREFIX)
    assert "guidance cut" in report.summary
    assert report.is_fake is True


def test_research_and_notify_emits_one_notification(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="investment_assistant.reporting"):
        report = research_and_notify(EVENT)

    notification_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "investment_assistant.reporting"
        and record.getMessage().startswith(EVENT_NOTIFICATION_PREFIX)
    ]
    assert report is not None
    assert notification_messages == [
        f"{EVENT_NOTIFICATION_PREFIX} | symbol=ACME | "
        f"event_time={EVENT_TIME.isoformat()} | report={report.summary}"
    ]
    assert FAKE_RESEARCH_PREFIX in notification_messages[0]


def test_no_event_runs_no_research_or_notification() -> None:
    def unexpected_research(_: SignificantEvent) -> ResearchReport:
        raise AssertionError("research must not run")

    def unexpected_notification(
        _: SignificantEvent,
        __: ResearchReport,
    ) -> None:
        raise AssertionError("notification must not run")

    assert (
        research_and_notify(
            None,
            researcher=unexpected_research,
            notifier=unexpected_notification,
        )
        is None
    )


def test_event_runs_research_and_notification_once() -> None:
    research_calls: list[SignificantEvent] = []
    notification_calls: list[tuple[SignificantEvent, ResearchReport]] = []
    expected_report = create_fake_research_report(EVENT)

    def record_research(event: SignificantEvent) -> ResearchReport:
        research_calls.append(event)
        return expected_report

    def record_notification(
        event: SignificantEvent,
        report: ResearchReport,
    ) -> None:
        notification_calls.append((event, report))

    report = research_and_notify(
        EVENT,
        researcher=record_research,
        notifier=record_notification,
    )

    assert report is expected_report
    assert research_calls == [EVENT]
    assert notification_calls == [(EVENT, expected_report)]
