"""Live delivery scheduling and status checks without network services."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr

from investment_assistant.clock import SteppingClock
from investment_assistant.config import Settings
from investment_assistant.discord_notify import DeliveryOutcome, SendResult
from investment_assistant.main import main
from investment_assistant.market_data import FakeMarketData, MarketSession
from investment_assistant.models import (
    Event,
    EventStatus,
    MarketTimeframe,
    MarketWindow,
    ResearchReport,
    SignalDirection,
    SignalImportance,
)
from investment_assistant.news import FakeNewsProvider, NewsProviderError
from investment_assistant.ops_log import HEARTBEAT_MESSAGE, operational_status
from investment_assistant.reporting import create_fake_research_report
from investment_assistant.storage import SQLiteStorage

NOW = datetime(2026, 2, 2, 21, 5, tzinfo=UTC)
WEBHOOK = "https://discord.com/api/webhooks/12345/test-token"


def _settings(path: Path, *, heartbeat: bool = False) -> Settings:
    return Settings(
        alpaca_api_key_id="test-id",
        alpaca_api_secret_key=SecretStr("test-secret"),
        discord_webhook_url=SecretStr(WEBHOOK),
        watchlist="TSLA",
        database_path=path,
        heartbeat=heartbeat,
    )


def _event(number: int, status: EventStatus) -> Event:
    at = NOW - timedelta(minutes=4 - number)
    return Event(
        event_id=f"event:{number}",
        ticker="TSLA",
        direction=SignalDirection.UP,
        category=None,
        importance=SignalImportance.MODERATE,
        market_windows=(MarketWindow.ONE_HOUR,),
        current_update=1,
        status=status,
        created_at=at,
        updated_at=at,
    )


def _session(open_: bool) -> MarketSession:
    return MarketSession(
        is_open=open_,
        timestamp=NOW,
        next_open=NOW + timedelta(days=1),
        next_close=NOW + timedelta(days=1, hours=6),
    )


def test_one_send_precedes_research_and_new_report_waits_for_next_pass(
    tmp_path: Path,
) -> None:
    path = tmp_path / "backlog.db"
    with SQLiteStorage(path) as storage:
        storage.initialize(now=NOW)
        for number in (1, 2):
            event = _event(number, EventStatus.REPORTED)
            storage.save_event(event)
            storage.save_report(create_fake_research_report(event, ()))
        storage.save_event(_event(3, EventStatus.QUEUED))
    order: list[str] = []

    def sender(event: Event, _report: ResearchReport, _identity: str) -> SendResult:
        order.append(f"send:{event.event_id}")
        return SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id=str(len(order)))

    def research(event: Event, _signals: object) -> ResearchReport:
        order.append(f"research:{event.event_id}")
        return create_fake_research_report(event, ())

    main(
        settings=_settings(path),
        provider=FakeMarketData(session=_session(False)),
        clock=SteppingClock(NOW),
        loop=True,
        max_cycles=2,
        sleeper=lambda _seconds: None,
        researcher=research,
        discord_sender=sender,
    )

    assert order == [
        "send:event:1",
        "research:event:3",
        "send:event:2",
        "send:event:3",
    ]
    with SQLiteStorage(path) as storage:
        storage.initialize(now=NOW)
        assert all(
            storage.get_event(f"event:{number}").status is EventStatus.NOTIFIED  # type: ignore[union-attr]
            for number in (1, 2, 3)
        )


@pytest.mark.parametrize("raise_error", [False, True])
def test_failed_or_timed_out_send_recovers_minutes_after_session_close(
    tmp_path: Path, raise_error: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "transition.db"
    with SQLiteStorage(path) as storage:
        storage.initialize(now=NOW)
        event = _event(1, EventStatus.REPORTED)
        storage.save_event(event)
        storage.save_report(create_fake_research_report(event, ()))

    class TrackingMarket(FakeMarketData):
        def __init__(self) -> None:
            super().__init__(session=_session(True))
            self.minute_fetches = 0
            self.stock_stream_is_open = True
            self.closed = False

        def fetch_history(self, **kwargs: object):  # type: ignore[no-untyped-def]
            if kwargs["timeframe"] is MarketTimeframe.ONE_MINUTE:
                self.minute_fetches += 1
            return super().fetch_history(**kwargs)  # type: ignore[arg-type]

        def close_stock_stream(self) -> None:
            self.closed = True
            self.stock_stream_is_open = False

    provider = TrackingMarket()
    clock = SteppingClock(NOW)

    def sender(_event: Event, _report: ResearchReport, _identity: str) -> SendResult:
        provider.set_session(_session(False))
        clock.advance_to(NOW + timedelta(seconds=20))
        if raise_error:
            raise TimeoutError("secret-token")
        return SendResult(DeliveryOutcome.DEFINITE_RETRY, safe_reason="CONNECT_FAILED")

    main(
        settings=_settings(path),
        provider=provider,
        clock=clock,
        loop=False,
        sleeper=lambda _seconds: None,
        researcher=lambda *_: pytest.fail("research should not run"),
        discord_sender=sender,
    )
    assert provider.minute_fetches >= 2  # startup and post-send recovery
    assert provider.closed
    output = capsys.readouterr().err
    assert WEBHOOK not in output
    assert "secret-token" not in output


def test_heartbeat_status_counts_current_backlog_and_separate_admission(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "status.db"
    settings = _settings(path, heartbeat=True)
    settings.daily_model_budget_usd = Decimal("0.10")
    settings.openai_api_key = SecretStr("model-secret")
    with SQLiteStorage(path) as storage:
        storage.initialize(now=NOW)
        for number in (1, 2):
            event = _event(number, EventStatus.REPORTED)
            storage.save_event(event)
            storage.save_report(create_fake_research_report(event, ()))
        status = operational_status(storage, settings, NOW)
        assert status.pending_notifications == 2
        assert status.oldest_pending_age_seconds == 180
        assert status.research_admission == "MODEL_BUDGET"
        assert status.classifier_admission == "AVAILABLE"
        assert status.research_required_usd == 1.62

    clock = SteppingClock(NOW)

    def sleeper(_seconds: float) -> None:
        clock.advance_to(clock.now() + timedelta(minutes=1))

    main(
        settings=settings,
        provider=FakeMarketData(session=_session(False)),
        clock=clock,
        loop=True,
        max_cycles=2,
        sleeper=sleeper,
        researcher=lambda *_: pytest.fail("research should not run"),
        discord_sender=lambda *_: SendResult(
            DeliveryOutcome.UNCERTAIN, safe_reason="TIMEOUT"
        ),
    )
    lines = capsys.readouterr().err.splitlines()
    beats = [json.loads(line) for line in lines if HEARTBEAT_MESSAGE in line]
    assert len(beats) == 1
    assert beats[0]["pending_notifications"] == 2
    assert beats[0]["uncertain_deliveries"] == 2
    assert beats[0]["uncertain_awaiting_resend"] == 2
    assert beats[0]["research_admission"] == "MODEL_BUDGET"
    assert beats[0]["classifier_admission"] == "AVAILABLE"
    assert "model-secret" not in "\n".join(lines)


def test_status_excludes_superseded_report_and_news_error_hides_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "safe.db"
    settings = _settings(path)
    with SQLiteStorage(path) as storage:
        storage.initialize(now=NOW)
        event = _event(1, EventStatus.REPORTED)
        storage.save_event(event)
        storage.save_report(create_fake_research_report(event, ()))
        assert operational_status(storage, settings, NOW).pending_notifications == 1
        storage.save_event(replace(event, current_update=2, status=EventStatus.QUEUED))
        assert operational_status(storage, settings, NOW).pending_notifications == 0

    settings.database_path = tmp_path / "news.db"
    main(
        settings=settings,
        provider=FakeMarketData(session=_session(False)),
        news_provider=FakeNewsProvider(error=NewsProviderError(WEBHOOK)),
        clock=SteppingClock(NOW),
        loop=False,
        sleeper=lambda _seconds: None,
        researcher=lambda *_: pytest.fail("research should not run"),
        discord_sender=lambda *_: pytest.fail("delivery should not run"),
    )
    assert WEBHOOK not in capsys.readouterr().err


def test_new_console_report_logs_missing_destination_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "console.db"
    settings = _settings(path)
    settings.discord_webhook_url = SecretStr("")
    with SQLiteStorage(path) as storage:
        storage.initialize(now=NOW)
        storage.save_event(_event(1, EventStatus.QUEUED))

    main(
        settings=settings,
        provider=FakeMarketData(session=_session(False)),
        clock=SteppingClock(NOW),
        loop=True,
        max_cycles=2,
        sleeper=lambda _seconds: None,
        researcher=lambda event, signals: create_fake_research_report(event, signals),
    )
    output = capsys.readouterr().err
    assert (
        output.count(
            '"message": "Discord not configured; using console for saved reports"'
        )
        == 1
    )
