"""Local recovery command routing without live services."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from investment_assistant.clock import FixedClock
from investment_assistant.config import Settings
from investment_assistant.delivery import DatabaseOwner, DeliveryManager, delivery_id
from investment_assistant.discord_notify import DeliveryOutcome, SendResult
from investment_assistant.models import (
    Event,
    EventStatus,
    MarketWindow,
    SignalDirection,
    SignalImportance,
)
from investment_assistant.notifications import run_notifications
from investment_assistant.reporting import create_fake_research_report
from investment_assistant.storage import SQLiteStorage

NOW = datetime(2026, 9, 24, 15, 0, tzinfo=UTC)
WEBHOOK = "https://discord.com/api/webhooks/12345/test-token"


def test_list_is_read_only_and_retry_requires_owner(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "commands.db"
    event = Event(
        event_id="event:held",
        ticker="TEST",
        direction=SignalDirection.DOWN,
        category=None,
        importance=SignalImportance.HIGH,
        market_windows=(MarketWindow.ONE_HOUR,),
        current_update=1,
        status=EventStatus.REPORTED,
        created_at=NOW,
        updated_at=NOW,
    )
    with SQLiteStorage(path) as storage:
        storage.initialize()
        storage.save_event(event)
        storage.save_report(create_fake_research_report(event, ()))
        manager = DeliveryManager(
            storage,
            clock=FixedClock(NOW),
            webhook_url=WEBHOOK,
            sender=lambda *_: SendResult(
                DeliveryOutcome.PERMANENT, safe_reason="HTTP_400"
            ),
        )
        assert manager.process_one()
    settings = Settings(database_path=path, discord_webhook_url=SecretStr(WEBHOOK))
    assert run_notifications(settings, ["list"], clock=FixedClock(NOW)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["delivery_id"] == delivery_id(event.event_id, 1)
    assert output["safe_reason"] == "HTTP_400"
    assert "FAKE RESEARCH" not in str(output)
    with DatabaseOwner(path):
        assert run_notifications(settings, ["list"], clock=FixedClock(NOW)) == 0
        capsys.readouterr()
        with pytest.raises(RuntimeError, match="already owned"):
            run_notifications(
                settings,
                [
                    "retry",
                    "--delivery-id",
                    output["delivery_id"],
                    "--accept-duplicate-risk",
                ],
                clock=FixedClock(NOW),
            )
    assert (
        run_notifications(
            settings,
            [
                "retry",
                "--delivery-id",
                output["delivery_id"],
                "--accept-duplicate-risk",
            ],
            clock=FixedClock(NOW),
        )
        == 0
    )
    with SQLiteStorage(path) as storage:
        storage.initialize()
        assert (
            storage._connection.execute(
                "SELECT manual_authorized FROM notification_deliveries WHERE delivery_id = ?",
                (output["delivery_id"],),
            ).fetchone()[0]
            == 1
        )
