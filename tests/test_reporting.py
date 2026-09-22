"""Tests for fake durable research and console notification."""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from investment_assistant.models import (
    Event,
    EventStatus,
    EvidenceSource,
    EvidenceText,
    LiveReportDetails,
    MarketSignal,
    MarketWindow,
    NewsSignal,
    ReportDraft,
    ResearchReport,
    ResearchUsage,
    SignalDirection,
    SignalImportance,
    SourceDetails,
)
from investment_assistant.reporting import (
    EVENT_NOTIFICATION_PREFIX,
    FAKE_RESEARCH_PREFIX,
    create_fake_research_report,
    emit_console_notification,
)

MARKET_TIME = datetime(2026, 1, 15, 15, 30, tzinfo=UTC)
EVENT_TIME = datetime(2026, 1, 15, 16, 30, tzinfo=UTC)
SOURCE_DETAILS = SourceDetails(
    provider="fixture",
    source="Fixture Wire",
    feed="offline-demo",
    retrieved_at=MARKET_TIME,
)
MARKET_SIGNAL = MarketSignal(
    signal_id="market-1",
    ticker="ACME",
    occurred_at=MARKET_TIME,
    importance=SignalImportance.HIGH,
    source_details=SOURCE_DETAILS,
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
    source_details=SOURCE_DETAILS,
    category="GUIDANCE",
    direction=SignalDirection.DOWN,
    matched_phrase="guidance cut",
    headline="Acme announces guidance cut",
)
EVENT = Event(
    event_id="event-1",
    ticker="ACME",
    direction=SignalDirection.DOWN,
    category=None,
    importance=SignalImportance.HIGH,
    market_windows=(MarketWindow.ONE_HOUR,),
    current_update=2,
    status=EventStatus.RESEARCHING,
    created_at=EVENT_TIME,
    updated_at=EVENT_TIME,
)


def test_creates_labeled_fake_research_report_for_current_update() -> None:
    report = create_fake_research_report(EVENT, (MARKET_SIGNAL, NEWS_SIGNAL))

    assert report.event_id == EVENT.event_id
    assert report.event_update == 2
    assert report.symbol == "ACME"
    assert report.event_occurred_at == EVENT_TIME
    assert report.summary.startswith(FAKE_RESEARCH_PREFIX)
    assert "2 qualifying signal(s)" in report.summary
    assert report.is_fake is True


def test_emits_one_console_notification(
    caplog: pytest.LogCaptureFixture,
) -> None:
    report = create_fake_research_report(EVENT, (MARKET_SIGNAL, NEWS_SIGNAL))

    with caplog.at_level(logging.INFO, logger="investment_assistant.reporting"):
        emit_console_notification(EVENT, report)

    notification_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "investment_assistant.reporting"
        and record.getMessage().startswith(EVENT_NOTIFICATION_PREFIX)
    ]
    assert notification_messages == [
        f"{EVENT_NOTIFICATION_PREFIX} | ticker=ACME | event_id=event-1 | "
        f"update=2 | report={report.summary}"
    ]
    assert FAKE_RESEARCH_PREFIX in report.summary


def test_emits_live_console_notification_with_posture_uncertainty_and_sources(
    caplog: pytest.LogCaptureFixture,
) -> None:
    details = LiveReportDetails(
        company=None,
        triggering_signal_ids=("market-1",),
        analysis=ReportDraft(
            summary="A saved price decline needs review; its cause is unknown.",
            likely_explanation=EvidenceText(
                text="Cause unknown from the local observations.",
                references=("signal:market-1",),
                is_hypothesis=False,
            ),
            competing_explanations=(),
            market_context=(),
            bullish_considerations=(),
            bearish_considerations=(),
            missing_information=("No corroborating company evidence.",),
            uncertainty="Price movement alone does not establish a cause.",
            scope="UNKNOWN",
            cause_unknown=True,
            evidence_character="UNKNOWN",
            confidence=0.2,
            posture="WAIT_FOR_CLARITY",
        ),
        sources=(
            EvidenceSource(
                reference="signal:market-1",
                title="ACME saved signal",
                identity="market-1",
                kind="packet",
                published_at=MARKET_TIME,
                retrieved_at=MARKET_TIME,
            ),
        ),
        model_version="gpt-5.4-mini-2026-03-17",
        prompt_version="research-v1",
        attempt_id="research:test",
        packet_as_of=EVENT_TIME,
        usage=ResearchUsage(),
    )
    report = ResearchReport(
        report_id="report:event-1:2",
        event_id=EVENT.event_id,
        event_update=EVENT.current_update,
        ticker=EVENT.ticker,
        event_occurred_at=EVENT.created_at,
        created_at=EVENT.updated_at,
        summary=details.analysis.summary,
        is_fake=False,
        details=details,
    )

    with caplog.at_level(logging.INFO, logger="investment_assistant.reporting"):
        emit_console_notification(EVENT, report)

    notification_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "investment_assistant.reporting"
        and record.getMessage().startswith(EVENT_NOTIFICATION_PREFIX)
    ]
    assert notification_messages == [
        f"{EVENT_NOTIFICATION_PREFIX} | ticker=ACME | event_id=event-1 | "
        f"update=2 | posture=WAIT_FOR_CLARITY | "
        f"uncertainty=Price movement alone does not establish a cause. | "
        f"sources=signal:market-1 | report={report.summary}"
    ]
    assert FAKE_RESEARCH_PREFIX not in report.summary
