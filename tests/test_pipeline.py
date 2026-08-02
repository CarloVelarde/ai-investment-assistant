"""End-to-end tests for the offline walking-skeleton pipeline."""

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from investment_assistant.clock import FixedClock
from investment_assistant.config import get_settings
from investment_assistant.main import main
from investment_assistant.models import ResearchReport, SignificantEvent
from investment_assistant.pipeline import run_pipeline
from investment_assistant.reporting import (
    EVENT_NOTIFICATION_PREFIX,
    FAKE_RESEARCH_PREFIX,
)

FIXTURE_DIR = (
    Path(__file__).parents[1]
    / "src"
    / "investment_assistant"
    / "fixtures"
    / "offline_walking_skeleton"
)
EVENT_TIME = datetime(2026, 1, 15, 16, 30, tzinfo=UTC)


def test_triggering_pipeline_creates_one_event_report_and_notification(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="investment_assistant.reporting"):
        result = run_pipeline(
            tracked_symbol="ACME",
            market_fixture_path=FIXTURE_DIR / "triggering_market.json",
            news_fixture_path=FIXTURE_DIR / "triggering_news.json",
            clock=FixedClock(EVENT_TIME),
        )

    notifications = [
        record.getMessage()
        for record in caplog.records
        if record.name == "investment_assistant.reporting"
        and record.getMessage().startswith(EVENT_NOTIFICATION_PREFIX)
    ]
    assert result.market_record.symbol == "ACME"
    assert result.news_record.symbol == "ACME"
    assert result.market_signal is not None
    assert result.news_signal is not None
    assert result.event is not None
    assert result.event.occurred_at == EVENT_TIME
    assert result.report is not None
    assert result.report.is_fake is True
    assert notifications == [
        f"{EVENT_NOTIFICATION_PREFIX} | symbol=ACME | "
        f"event_time={EVENT_TIME.isoformat()} | report={result.report.summary}"
    ]


def test_non_triggering_pipeline_runs_no_research_or_notification() -> None:
    research_calls: list[SignificantEvent] = []
    notification_calls: list[tuple[SignificantEvent, ResearchReport]] = []

    def record_research(event: SignificantEvent) -> ResearchReport:
        research_calls.append(event)
        raise AssertionError("research must not run")

    def record_notification(
        event: SignificantEvent,
        report: ResearchReport,
    ) -> None:
        notification_calls.append((event, report))

    result = run_pipeline(
        tracked_symbol="ACME",
        market_fixture_path=FIXTURE_DIR / "non_triggering_market.json",
        news_fixture_path=FIXTURE_DIR / "non_triggering_news.json",
        clock=FixedClock(EVENT_TIME),
        researcher=record_research,
        notifier=record_notification,
    )

    assert result.market_signal is not None
    assert result.news_signal is None
    assert result.event is None
    assert result.report is None
    assert research_calls == []
    assert notification_calls == []


def test_console_entrypoint_runs_bundled_triggering_scenario(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("INVESTMENT_ASSISTANT_LOG_JSON", "false")
    try:
        main()
    finally:
        get_settings.cache_clear()

    standard_error = capsys.readouterr().err
    assert "Application started" in standard_error
    assert standard_error.count(EVENT_NOTIFICATION_PREFIX) == 1
    assert FAKE_RESEARCH_PREFIX in standard_error
