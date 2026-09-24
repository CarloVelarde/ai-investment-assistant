"""Durable webhook submission, restart, and migration behavior."""

import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from investment_assistant.clock import SteppingClock
from investment_assistant.delivery import (
    DatabaseOwner,
    DeliveryManager,
    DeliveryState,
    delivery_id,
    list_deliveries,
)
from investment_assistant.discord_notify import DeliveryOutcome, SendResult
from investment_assistant.event_manager import EventManager
from investment_assistant.models import (
    Event,
    EventStatus,
    MarketWindow,
    ResearchReport,
    SignalDirection,
    SignalImportance,
)
from investment_assistant.reporting import (
    create_fake_research_report,
    emit_console_notification,
)
from investment_assistant.storage import DATABASE_VERSION, SQLiteStorage

NOW = datetime(2026, 9, 23, 15, 0, tzinfo=UTC)
WEBHOOK = "https://discord.com/api/webhooks/12345/test-token"


def _seed(storage: SQLiteStorage, *, update: int = 1) -> Event:
    event = Event(
        event_id="event:delivery-test",
        ticker="TEST",
        direction=SignalDirection.UP,
        category=None,
        importance=SignalImportance.MODERATE,
        market_windows=(MarketWindow.ONE_HOUR,),
        current_update=update,
        status=EventStatus.REPORTED,
        created_at=NOW,
        updated_at=NOW,
    )
    storage.save_event(event)
    storage.save_report(create_fake_research_report(event, ()))
    return event


def _manager(
    storage: SQLiteStorage,
    clock: SteppingClock,
    calls: list[str],
    outcomes: list[SendResult],
) -> DeliveryManager:
    def send(_: Event, __: ResearchReport, identity: str) -> SendResult:
        calls.append(identity)
        return outcomes.pop(0)

    return DeliveryManager(storage, clock=clock, webhook_url=WEBHOOK, sender=send)


def _state(storage: SQLiteStorage, event: Event) -> sqlite3.Row:
    row = storage._connection.execute(
        "SELECT * FROM notification_deliveries WHERE event_id = ? AND event_update = ?",
        (event.event_id, event.current_update),
    ).fetchone()
    assert row is not None
    return cast(sqlite3.Row, row)


def test_success_is_claimed_before_send_and_never_posted_twice(tmp_path: Path) -> None:
    path = tmp_path / "success.db"
    calls: list[str] = []
    clock = SteppingClock(NOW)
    with SQLiteStorage(path) as storage:
        storage.initialize()
        event = _seed(storage)

        def send(_: Event, __: ResearchReport, identity: str) -> SendResult:
            calls.append(identity)
            assert _state(storage, event)["state"] == "CLAIMED"
            attempt = storage._connection.execute(
                "SELECT outcome FROM notification_submissions WHERE delivery_id = ?",
                (identity,),
            ).fetchone()
            assert attempt is not None and attempt["outcome"] is None
            return SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id="98765")

        manager = DeliveryManager(
            storage, clock=clock, webhook_url=WEBHOOK, sender=send
        )
        assert manager.process_one()
        assert _state(storage, event)["state"] == "SUCCEEDED"
        delivery = manager.get_delivery(delivery_id(event.event_id, 1))
        assert delivery is not None and delivery.state is DeliveryState.SUCCEEDED
        submissions = manager.list_submissions(delivery.delivery_id)
        assert len(submissions) == 1 and submissions[0].message_id == "98765"
        assert storage.get_event(event.event_id).status is EventStatus.NOTIFIED  # type: ignore[union-attr]
        assert not manager.process_one()
    with SQLiteStorage(path) as storage:
        storage.initialize()
        assert not _manager(storage, clock, calls, []).process_one()
    assert calls == [delivery_id(event.event_id, 1)]


def test_confirmation_cancels_pending_resend_and_requires_matching_receipt(
    tmp_path: Path,
) -> None:
    clock = SteppingClock(NOW)
    calls: list[str] = []
    lookups: list[tuple[str, str]] = []
    with SQLiteStorage(tmp_path / "confirm.db") as storage:
        storage.initialize()
        event = _seed(storage)
        identity = delivery_id(event.event_id, 1)
        manager = _manager(
            storage,
            clock,
            calls,
            [SendResult(DeliveryOutcome.UNCERTAIN, safe_reason="TIMEOUT")],
        )
        assert manager.process_one()
        assert not manager.authorize_retry(identity)
        assert identity in {item.delivery_id for item in list_deliveries(storage)}

        def lookup(message_id: str, requested: str) -> SendResult:
            lookups.append((message_id, requested))
            return SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id=message_id)

        manager = DeliveryManager(
            storage,
            clock=clock,
            webhook_url=WEBHOOK,
            sender=lambda *_: pytest.fail("unexpected resend"),
            lookup=lookup,
        )
        assert not manager.confirm_receipt(identity, "bad-id")
        assert manager.confirm_receipt(identity, "123456")
        assert manager.confirm_receipt(identity, "123456")
        clock.advance_to(NOW + timedelta(hours=1))
        assert not manager.process_one()
        assert manager.get_delivery(identity).state is DeliveryState.SUCCEEDED  # type: ignore[union-attr]
        assert lookups == [("123456", identity)]
        assert storage.get_event(event.event_id).last_notified_at == NOW  # type: ignore[union-attr]


def test_confirmation_retains_superseded_receipt_without_notifying_new_update(
    tmp_path: Path,
) -> None:
    clock = SteppingClock(NOW)
    with SQLiteStorage(tmp_path / "stale-confirm.db") as storage:
        storage.initialize()
        event = _seed(storage)
        identity = delivery_id(event.event_id, 1)
        manager = _manager(
            storage,
            clock,
            [],
            [SendResult(DeliveryOutcome.UNCERTAIN, safe_reason="TIMEOUT")],
        )
        assert manager.process_one()
        storage.save_event(replace(event, current_update=2, status=EventStatus.QUEUED))
        manager = DeliveryManager(
            storage,
            clock=clock,
            webhook_url=WEBHOOK,
            sender=lambda *_: pytest.fail("no POST"),
            lookup=lambda message_id, _: SendResult(
                DeliveryOutcome.ACKNOWLEDGED, message_id=message_id
            ),
        )
        assert manager.confirm_receipt(identity, "567")
        record = manager.get_delivery(identity)
        assert record is not None and record.state is DeliveryState.SUPERSEDED
        assert record.message_id == "567"
        current = storage.get_event(event.event_id)
        assert current is not None and current.current_update == 2
        assert current.status is EventStatus.QUEUED


def test_receipt_lookup_rate_limit_holds_other_work(tmp_path: Path) -> None:
    clock = SteppingClock(NOW)
    with SQLiteStorage(tmp_path / "lookup-wait.db") as storage:
        storage.initialize()
        event = _seed(storage)
        identity = delivery_id(event.event_id, 1)
        first = _manager(
            storage,
            clock,
            [],
            [SendResult(DeliveryOutcome.UNCERTAIN, safe_reason="TIMEOUT")],
        )
        assert first.process_one()
        manager = DeliveryManager(
            storage,
            clock=clock,
            webhook_url=WEBHOOK,
            sender=lambda *_: pytest.fail("provider wait"),
            lookup=lambda *_: SendResult(
                DeliveryOutcome.DEFINITE_RETRY,
                safe_reason="HTTP_429",
                wait_seconds=Decimal("1200"),
                global_wait=True,
            ),
        )
        assert not manager.confirm_receipt(identity, "123")
        clock.advance_to(NOW + timedelta(minutes=15))
        assert not manager.process_one()
        clock.advance_to(NOW + timedelta(minutes=20))
        after_wait = _manager(
            storage,
            clock,
            [],
            [SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id="123")],
        )
        assert after_wait.process_one()


def test_manual_retry_is_one_submission_and_respects_global_wait(
    tmp_path: Path,
) -> None:
    clock = SteppingClock(NOW)
    calls: list[str] = []
    with SQLiteStorage(tmp_path / "manual.db") as storage:
        storage.initialize()
        event = _seed(storage)
        identity = delivery_id(event.event_id, 1)
        manager = _manager(
            storage,
            clock,
            calls,
            [SendResult(DeliveryOutcome.PERMANENT, safe_reason="HTTP_400")],
        )
        assert not manager.authorize_retry(identity)
        assert manager.process_one()
        assert manager.authorize_retry(identity)
        assert manager.authorize_retry(identity)
        storage._connection.execute(
            "INSERT INTO notification_global_state VALUES (1, ?)",
            ((NOW + timedelta(minutes=5)).isoformat(),),
        )
        storage._connection.commit()
        assert not manager.process_one()
        clock.advance_to(NOW + timedelta(minutes=5))
        failed = _manager(
            storage,
            clock,
            calls,
            [SendResult(DeliveryOutcome.DEFINITE_RETRY, safe_reason="CONNECT")],
        )
        assert failed.process_one()
        assert failed.get_delivery(identity).state is DeliveryState.PERMANENT_FAILURE  # type: ignore[union-attr]
        assert not failed.process_one()
        assert failed.authorize_retry(identity)
        succeeded = _manager(
            storage,
            clock,
            calls,
            [SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id="77")],
        )
        assert succeeded.process_one()
        assert [
            item.attempt_number for item in succeeded.list_submissions(identity)
        ] == [1, 2, 3]
        assert identity in {item.delivery_id for item in list_deliveries(storage)}
        assert not succeeded.authorize_retry(identity)
        assert len(calls) == 3


def test_old_webhook_receipt_after_changed_destination_retry(tmp_path: Path) -> None:
    clock = SteppingClock(NOW)
    other_webhook = "https://discord.com/api/webhooks/99999/other-token"
    with SQLiteStorage(tmp_path / "changed.db") as storage:
        storage.initialize()
        event = _seed(storage)
        identity = delivery_id(event.event_id, 1)
        first = _manager(
            storage,
            clock,
            [],
            [SendResult(DeliveryOutcome.UNCERTAIN, safe_reason="TIMEOUT")],
        )
        assert first.process_one()
        clock.advance_to(NOW + timedelta(minutes=15))
        first = _manager(
            storage,
            clock,
            [],
            [SendResult(DeliveryOutcome.DEFINITE_RETRY, safe_reason="CONNECT")],
        )
        assert first.process_one()
        changed = DeliveryManager(
            storage,
            clock=clock,
            webhook_url=other_webhook,
            sender=lambda *_: SendResult(
                DeliveryOutcome.PERMANENT, safe_reason="HTTP_400"
            ),
        )
        assert changed.authorize_retry(identity)
        assert changed.process_one()
        restored = DeliveryManager(
            storage,
            clock=clock,
            webhook_url=WEBHOOK,
            sender=lambda *_: pytest.fail("no POST"),
            lookup=lambda message_id, _: SendResult(
                DeliveryOutcome.ACKNOWLEDGED, message_id=message_id
            ),
        )
        assert restored.confirm_receipt(identity, "456")
        delivery = restored.get_delivery(identity)
        assert delivery is not None and delivery.state is DeliveryState.SUCCEEDED


def test_v6_submission_history_survives_operator_retry_schema_upgrade(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v6-submissions.db"
    clock = SteppingClock(NOW)
    with SQLiteStorage(path) as storage:
        storage.initialize()
        event = _seed(storage)
        manager = _manager(
            storage,
            clock,
            [],
            [SendResult(DeliveryOutcome.PERMANENT, safe_reason="HTTP_400")],
        )
        assert manager.process_one()
        identity = delivery_id(event.event_id, 1)
        prior = manager.list_submissions(identity)[0]
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE notification_submissions")
        connection.execute(
            """CREATE TABLE notification_submissions (
            attempt_id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL,
            attempt_number INTEGER NOT NULL CHECK (attempt_number BETWEEN 1 AND 6),
            resend INTEGER NOT NULL, claimed_at TEXT NOT NULL,
            completed_at TEXT, outcome TEXT, message_id TEXT, safe_reason TEXT)"""
        )
        connection.execute(
            """INSERT INTO notification_submissions VALUES (?, ?, 1, 0, ?, ?, 'PERMANENT', NULL, 'HTTP_400')""",
            (prior.attempt_id, identity, NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute("PRAGMA user_version = 6")
    with SQLiteStorage(path) as storage:
        storage.initialize(now=NOW)
        manager = _manager(storage, clock, [], [])
        assert manager.list_submissions(identity)[0].destination_fingerprint is not None
        assert manager.authorize_retry(identity)
        manager = _manager(
            storage,
            clock,
            [],
            [SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id="123")],
        )
        assert manager.process_one()
        assert [item.attempt_number for item in manager.list_submissions(identity)] == [
            1,
            2,
        ]


def test_console_completion_precedes_best_effort_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_assistant.event_manager import EventManager

    with SQLiteStorage(tmp_path / "console.db") as storage:
        storage.initialize()
        event = _seed(storage)
        report = storage.get_report_for_update(event.event_id, 1)
        assert report is not None and "FAKE RESEARCH" in report.summary

        def output(*_: object, **__: object) -> None:
            assert storage.get_event(event.event_id).status is EventStatus.NOTIFIED  # type: ignore[union-attr]
            raise RuntimeError("output failed")

        monkeypatch.setattr("investment_assistant.reporting.logger.info", output)
        manager = EventManager(storage, clock=SteppingClock(NOW))
        result = manager.process_event(
            event.event_id,
            researcher=lambda *_: pytest.fail("research"),
            notifier=emit_console_notification,
        )
        assert result is not None and result.status is EventStatus.NOTIFIED
        assert storage.get_event(event.event_id).last_notified_at == NOW  # type: ignore[union-attr]
        repeated = manager.process_event(
            event.event_id,
            researcher=lambda *_: pytest.fail("research"),
            notifier=emit_console_notification,
        )
        assert repeated is not None and repeated.status is EventStatus.NOTIFIED


def test_saved_report_delivers_with_zero_model_budget_and_stale_retry_is_rejected(
    tmp_path: Path,
) -> None:
    clock = SteppingClock(NOW)
    with SQLiteStorage(tmp_path / "zero-budget.db") as storage:
        storage.initialize()
        event = _seed(storage)
        assert (
            storage.reserve_classifier_call(
                now=NOW,
                model_version="gpt-5.4-nano-2026-03-17",
                daily_calls=100,
                budget_microdollars=0,
            )
            is None
        )
        manager = _manager(
            storage,
            clock,
            [],
            [SendResult(DeliveryOutcome.PERMANENT, safe_reason="HTTP_400")],
        )
        assert manager.process_one()
        identity = delivery_id(event.event_id, 1)
        storage.save_event(replace(event, current_update=2, status=EventStatus.QUEUED))
        assert not manager.authorize_retry(identity)
        assert manager.get_delivery(identity).state is DeliveryState.SUPERSEDED  # type: ignore[union-attr]

    with SQLiteStorage(tmp_path / "zero-budget-deliver.db") as storage:
        storage.initialize()
        event = _seed(storage)
        assert (
            storage.reserve_classifier_call(
                now=NOW,
                model_version="gpt-5.4-nano-2026-03-17",
                daily_calls=100,
                budget_microdollars=0,
            )
            is None
        )
        manager = _manager(
            storage,
            clock,
            [],
            [SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id="999")],
        )
        assert manager.process_one()
        assert storage.get_event(event.event_id).status is EventStatus.NOTIFIED  # type: ignore[union-attr]


def test_unresolved_claim_waits_once_after_restart_and_resends_once(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unknown.db"
    clock = SteppingClock(NOW)
    calls: list[str] = []
    with SQLiteStorage(path) as storage:
        storage.initialize()
        event = _seed(storage)
        manager = _manager(storage, clock, calls, [])
        manager._discover_and_supersede(NOW)
        claim = manager._claim_next(NOW)
        assert claim is not None
        assert _state(storage, event)["state"] == "CLAIMED"
    clock.advance_to(NOW + timedelta(minutes=2))
    with SQLiteStorage(path) as storage:
        storage.initialize()
        manager = _manager(
            storage,
            clock,
            calls,
            [SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id="22")],
        )
        assert not manager.process_one()
        deadline = _state(storage, event)["resend_not_before"]
        assert deadline == (NOW + timedelta(minutes=17)).isoformat()
        clock.advance_to(NOW + timedelta(minutes=16, seconds=59))
        assert not manager.process_one()
        assert _state(storage, event)["resend_not_before"] == deadline
        clock.advance_to(NOW + timedelta(minutes=17))
        assert manager.process_one()
        assert _state(storage, event)["uncertain_resend_used"] == 1
        assert _state(storage, event)["state"] == "SUCCEEDED"
    assert len(calls) == 1


def test_receipt_saved_before_completion_finishes_locally_after_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "receipt.db"
    clock = SteppingClock(NOW)
    calls: list[str] = []
    with SQLiteStorage(path) as storage:
        storage.initialize()
        event = _seed(storage)
        manager = _manager(storage, clock, calls, [])
        manager._discover_and_supersede(NOW)
        claim = manager._claim_next(NOW)
        assert claim is not None
        manager._save_receipt(
            claim, SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id="333"), NOW
        )
        assert _state(storage, event)["state"] == "ACKNOWLEDGED"
    with SQLiteStorage(path) as storage:
        storage.initialize()
        assert not _manager(storage, clock, calls, []).process_one()
        assert _state(storage, event)["state"] == "SUCCEEDED"
    assert calls == []


def test_completed_delivery_is_not_resent_if_output_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "output-gap.db"
    clock = SteppingClock(NOW)
    calls: list[str] = []

    def fail_output(*_: object, **__: object) -> None:
        raise RuntimeError("output unavailable")

    with SQLiteStorage(path) as storage:
        storage.initialize()
        event = _seed(storage)
        monkeypatch.setattr("investment_assistant.delivery.logger.info", fail_output)
        with pytest.raises(RuntimeError, match="output unavailable"):
            _manager(
                storage,
                clock,
                calls,
                [SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id="555")],
            ).process_one()
        assert _state(storage, event)["state"] == "SUCCEEDED"
    with SQLiteStorage(path) as storage:
        storage.initialize()
        assert not _manager(storage, clock, calls, []).process_one()
    assert len(calls) == 1


def test_uncertain_resend_failure_stops_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "resend.db"
    clock = SteppingClock(NOW)
    calls: list[str] = []
    with SQLiteStorage(path) as storage:
        storage.initialize()
        event = _seed(storage)
        manager = _manager(
            storage,
            clock,
            calls,
            [SendResult(DeliveryOutcome.UNCERTAIN, safe_reason="TIMEOUT")],
        )
        assert manager.process_one()
        assert _state(storage, event)["state"] == "UNCERTAIN"
        clock.advance_to(NOW + timedelta(minutes=15))
        manager = _manager(
            storage,
            clock,
            calls,
            [SendResult(DeliveryOutcome.DEFINITE_RETRY, safe_reason="CONNECT")],
        )
        assert manager.process_one()
        assert _state(storage, event)["state"] == "UNCERTAIN"
        assert _state(storage, event)["uncertain_resend_used"] == 1
    with SQLiteStorage(path) as storage:
        storage.initialize()
        assert not _manager(storage, clock, calls, []).process_one()
    assert len(calls) == 2


def test_five_definite_failures_use_one_two_four_eight_minute_waits(
    tmp_path: Path,
) -> None:
    clock = SteppingClock(NOW)
    calls: list[str] = []
    with SQLiteStorage(tmp_path / "backoff.db") as storage:
        storage.initialize()
        event = _seed(storage)
        manager = _manager(
            storage,
            clock,
            calls,
            [SendResult(DeliveryOutcome.DEFINITE_RETRY)] * 5,
        )
        for number, minutes in enumerate((1, 2, 4, 8, 0), start=1):
            assert manager.process_one()
            row = _state(storage, event)
            assert row["ordinary_count"] == number
            if minutes:
                assert row["state"] == "RETRY_WAIT"
                assert not manager.process_one()
                clock.advance_to(clock.now() + timedelta(minutes=minutes))
            else:
                assert row["state"] == "PERMANENT_FAILURE"
        assert not manager.process_one()
    assert len(calls) == 5


def test_provider_wait_blocks_other_reports_and_survives_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wait.db"
    clock = SteppingClock(NOW)
    calls: list[str] = []
    with SQLiteStorage(path) as storage:
        storage.initialize()
        _seed(storage)
        manager = _manager(
            storage,
            clock,
            calls,
            [SendResult(DeliveryOutcome.DEFINITE_RETRY, wait_seconds=Decimal(1200))],
        )
        assert manager.process_one()
        second = replace(_seed(storage, update=2), event_id="event:other")
        storage.save_event(second)
        storage.save_report(create_fake_research_report(second, ()))
    with SQLiteStorage(path) as storage:
        storage.initialize()
        manager = _manager(
            storage,
            clock,
            calls,
            [SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id="444")],
        )
        assert not manager.process_one()
        clock.advance_to(NOW + timedelta(minutes=20))
        assert manager.process_one()
    assert len(calls) == 2


def test_migration_preserves_console_history_and_holds_unknown_spend(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v5.db"
    with SQLiteStorage(path) as storage:
        storage.initialize()
        event = _seed(storage)
        prior_research = replace(
            event,
            event_id="event:prior-research",
            status=EventStatus.QUEUED,
            last_notified_at=None,
        )
        storage.save_event(prior_research)
        assert (
            storage.reserve_research_attempt(
                prior_research.event_id,
                1,
                now=NOW,
                model_version="model-v1",
                prompt_version="prompt-v1",
                has_api_key=True,
            )
            is not None
        )
        storage._connection.execute(
            """INSERT INTO notification_attempts
            (attempt_id, event_id, event_update, attempted_at, succeeded)
            VALUES ('old-success', ?, 1, ?, 1)""",
            (event.event_id, NOW.isoformat()),
        )
        storage._connection.execute(
            """INSERT INTO notification_attempts
            (attempt_id, event_id, event_update, attempted_at, succeeded, safe_error)
            VALUES ('old-failure', ?, 1, ?, 0, 'legacy console error')""",
            (event.event_id, (NOW - timedelta(minutes=1)).isoformat()),
        )
        storage._connection.execute(
            "INSERT INTO classifier_budget (utc_day, call_count) VALUES (?, 4)",
            (NOW.date().isoformat(),),
        )
        storage._connection.commit()
    with sqlite3.connect(path) as connection:
        for table in (
            "notification_deliveries",
            "notification_submissions",
            "notification_destination_state",
            "notification_global_state",
            "model_budget_migration_hold",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version = 5")
    with SQLiteStorage(path) as storage:
        storage.initialize(now=NOW)
        assert storage.database_version == DATABASE_VERSION == 7
        assert _state(storage, event)["state"] == "SUCCEEDED"
        assert _state(storage, event)["destination"] == "console"
        assert not _manager(storage, SteppingClock(NOW), [], []).process_one()
        assert len(storage.list_notification_attempts(event.event_id)) == 2
        assert storage.research_starts_on(NOW) == 1
        assert (
            storage._connection.execute(
                "SELECT call_count FROM classifier_budget WHERE utc_day = ?",
                (NOW.date().isoformat(),),
            ).fetchone()[0]
            == 4
        )
        assert (
            storage._connection.execute(
                "SELECT reason FROM model_budget_migration_hold WHERE utc_day = ?",
                (NOW.date().isoformat(),),
            ).fetchone()[0]
            == "LEGACY_SPEND_UNKNOWN"
        )
        assert storage.model_budget_migration_hold_on(NOW)
        assert not storage.model_budget_migration_hold_on(NOW + timedelta(days=1))
        queued = replace(
            event,
            event_id="event:queued",
            status=EventStatus.QUEUED,
            last_notified_at=None,
        )
        storage.save_event(queued)
        assert (
            storage.reserve_research_attempt(
                queued.event_id,
                queued.current_update,
                now=NOW,
                model_version="model-v1",
                prompt_version="prompt-v1",
                has_api_key=True,
            )
            is None
        )
        assert storage.get_research_deferral(queued.event_id, 1) is not None
        storage.initialize(now=NOW)
        assert (
            storage._connection.execute(
                "SELECT COUNT(*) FROM notification_deliveries"
            ).fetchone()[0]
            == 1
        )


def test_database_owner_rejects_second_process_and_releases_after_exit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "owner.db"
    with (
        DatabaseOwner(path),
        pytest.raises(RuntimeError, match="already owned"),
        DatabaseOwner(path),
    ):
        pass
    script = (
        "from pathlib import Path; from investment_assistant.delivery import DatabaseOwner; "
        f"with DatabaseOwner(Path({str(path)!r})): pass"
    )
    # A new process can take the lock after the original owner exits.
    result = subprocess.run(
        [sys.executable, "-c", script.replace("; with", "\nwith")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_database_lock_releases_when_owner_process_is_killed(tmp_path: Path) -> None:
    path = tmp_path / "killed-owner.db"
    script = (
        "from pathlib import Path\n"
        "from investment_assistant.delivery import DatabaseOwner\n"
        "import time\n"
        f"with DatabaseOwner(Path({str(path)!r})):\n"
        "    print('READY', flush=True)\n"
        "    time.sleep(30)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "READY"
        with pytest.raises(RuntimeError, match="already owned"), DatabaseOwner(path):
            pass
    finally:
        process.kill()
        process.communicate(timeout=5)
    with DatabaseOwner(path):
        pass


def test_event_manager_defers_new_report_delivery_until_next_pass(
    tmp_path: Path,
) -> None:
    clock = SteppingClock(NOW)
    calls: list[str] = []
    with SQLiteStorage(tmp_path / "manager.db") as storage:
        storage.initialize()
        event = _seed(storage)
        storage.save_event(replace(event, status=EventStatus.QUEUED))
        manager = EventManager(
            storage,
            clock=clock,
            delivery_manager=_manager(
                storage,
                clock,
                calls,
                [SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id="555")],
            ),
        )
        # Existing report becomes deliverable after the next pass, without research.
        storage.save_event(event)
        assert (
            manager.process_pending(
                researcher=lambda *_: pytest.fail(), notifier=lambda *_: pytest.fail()
            )
            == ()
        )
        assert calls == [delivery_id(event.event_id, 1)]


def test_fifth_attempt_uncertainty_allows_only_one_sixth_submission(
    tmp_path: Path,
) -> None:
    clock = SteppingClock(NOW)
    calls: list[str] = []
    with SQLiteStorage(tmp_path / "six.db") as storage:
        storage.initialize()
        event = _seed(storage)
        manager = _manager(
            storage,
            clock,
            calls,
            [SendResult(DeliveryOutcome.DEFINITE_RETRY)] * 4
            + [SendResult(DeliveryOutcome.UNCERTAIN)]
            + [SendResult(DeliveryOutcome.UNCERTAIN)],
        )
        for minutes in (1, 2, 4, 8):
            assert manager.process_one()
            clock.advance_to(clock.now() + timedelta(minutes=minutes))
        assert manager.process_one()
        assert _state(storage, event)["ordinary_count"] == 5
        clock.advance_to(clock.now() + timedelta(minutes=15))
        assert manager.process_one()
        assert _state(storage, event)["uncertain_resend_used"] == 1
        assert not manager.process_one()
    assert len(calls) == 6


def test_crash_after_consuming_resend_allowance_does_not_replenish_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "resend-crash.db"
    clock = SteppingClock(NOW)
    calls: list[str] = []
    with SQLiteStorage(path) as storage:
        storage.initialize()
        event = _seed(storage)
        manager = _manager(
            storage, clock, calls, [SendResult(DeliveryOutcome.UNCERTAIN)]
        )
        assert manager.process_one()
        clock.advance_to(NOW + timedelta(minutes=15))
        claim = manager._claim_next(clock.now())
        assert claim is not None and claim.resend
        assert _state(storage, event)["uncertain_resend_used"] == 1
    with SQLiteStorage(path) as storage:
        storage.initialize()
        assert not _manager(storage, clock, calls, []).process_one()
        assert _state(storage, event)["state"] == "UNCERTAIN"
        assert _state(storage, event)["uncertain_resend_used"] == 1
    assert len(calls) == 1


def test_longer_provider_wait_delays_uncertain_resend(tmp_path: Path) -> None:
    clock = SteppingClock(NOW)
    calls: list[str] = []
    with SQLiteStorage(tmp_path / "long-wait.db") as storage:
        storage.initialize()
        event = _seed(storage)
        manager = _manager(
            storage,
            clock,
            calls,
            [
                SendResult(DeliveryOutcome.UNCERTAIN, wait_seconds=Decimal(1800)),
                SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id="99"),
            ],
        )
        assert manager.process_one()
        clock.advance_to(NOW + timedelta(minutes=15))
        assert not manager.process_one()
        clock.advance_to(NOW + timedelta(minutes=30))
        assert manager.process_one()
        assert _state(storage, event)["state"] == "SUCCEEDED"
    assert len(calls) == 2


def test_stale_receipt_does_not_mark_new_update_notified(tmp_path: Path) -> None:
    clock = SteppingClock(NOW)
    calls: list[str] = []
    with SQLiteStorage(tmp_path / "stale.db") as storage:
        storage.initialize()
        event = _seed(storage)

        def send(_: Event, __: ResearchReport, identity: str) -> SendResult:
            calls.append(identity)
            storage.save_event(
                replace(event, current_update=2, status=EventStatus.QUEUED)
            )
            return SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id="1234")

        manager = DeliveryManager(
            storage, clock=clock, webhook_url=WEBHOOK, sender=send
        )
        assert manager.process_one()
        assert _state(storage, event)["state"] == "SUCCEEDED"
        current = storage.get_event(event.event_id)
        assert current is not None and current.status is EventStatus.QUEUED
        assert current.last_notified_at is None
        assert not manager.process_one()
    assert len(calls) == 1


def test_refused_claim_makes_no_http_call(tmp_path: Path) -> None:
    clock = SteppingClock(NOW)
    calls: list[str] = []
    with SQLiteStorage(tmp_path / "refused.db") as storage:
        storage.initialize()
        _seed(storage)
        storage._connection.execute(
            """CREATE TRIGGER refuse_claim BEFORE UPDATE ON notification_deliveries
            WHEN NEW.state = 'CLAIMED' BEGIN SELECT RAISE(IGNORE); END"""
        )
        assert not _manager(storage, clock, calls, []).process_one()
    assert calls == []


def test_changed_webhook_does_not_resend_uncertain_delivery(tmp_path: Path) -> None:
    path = tmp_path / "changed.db"
    clock = SteppingClock(NOW)
    calls: list[str] = []
    with SQLiteStorage(path) as storage:
        storage.initialize()
        event = _seed(storage)
        assert _manager(
            storage, clock, calls, [SendResult(DeliveryOutcome.UNCERTAIN)]
        ).process_one()
    clock.advance_to(NOW + timedelta(minutes=20))
    with SQLiteStorage(path) as storage:
        storage.initialize()
        changed = DeliveryManager(
            storage,
            clock=clock,
            webhook_url="https://discord.com/api/webhooks/12345/different-token",
            sender=lambda *_: pytest.fail("changed destination sent old report"),
        )
        assert not changed.process_one()
        assert _state(storage, event)["state"] == "UNCERTAIN"
    assert len(calls) == 1


def test_removing_webhook_holds_uncertain_delivery_out_of_console_path(
    tmp_path: Path,
) -> None:
    path = tmp_path / "removed.db"
    clock = SteppingClock(NOW)
    with SQLiteStorage(path) as storage:
        storage.initialize()
        event = _seed(storage)
        assert _manager(
            storage, clock, [], [SendResult(DeliveryOutcome.UNCERTAIN)]
        ).process_one()
    with SQLiteStorage(path) as storage:
        storage.initialize()
        calls: list[int] = []
        manager = EventManager(storage, clock=clock)
        assert (
            manager.process_pending(
                researcher=lambda *_: pytest.fail("research repeated"),
                notifier=lambda current, _: calls.append(current.current_update),
                max_research_runs=1,
            )
            == ()
        )
        assert storage.get_event(event.event_id).status is EventStatus.REPORTED  # type: ignore[union-attr]
        assert calls == []


def test_global_rate_limit_wait_survives_restart_and_changed_webhook(
    tmp_path: Path,
) -> None:
    path = tmp_path / "global.db"
    clock = SteppingClock(NOW)
    with SQLiteStorage(path) as storage:
        storage.initialize()
        _seed(storage)
        assert _manager(
            storage,
            clock,
            [],
            [
                SendResult(
                    DeliveryOutcome.DEFINITE_RETRY,
                    safe_reason="HTTP_429",
                    wait_seconds=Decimal(600),
                    global_wait=True,
                )
            ],
        ).process_one()
    with SQLiteStorage(path) as storage:
        storage.initialize()
        second = replace(_seed(storage, update=2), event_id="event:other")
        storage.save_event(second)
        storage.save_report(create_fake_research_report(second, ()))
        changed = DeliveryManager(
            storage,
            clock=clock,
            webhook_url="https://discord.com/api/webhooks/12345/different-token",
            sender=lambda *_: pytest.fail("global hold bypassed"),
        )
        assert not changed.process_one()
        clock.advance_to(NOW + timedelta(minutes=10))
        calls: list[str] = []

        def send_new(_: Event, __: ResearchReport, identity: str) -> SendResult:
            calls.append(identity)
            return SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id="777")

        changed = DeliveryManager(
            storage,
            clock=clock,
            webhook_url="https://discord.com/api/webhooks/12345/different-token",
            sender=send_new,
        )
        assert changed.process_one()
        assert calls == [delivery_id(second.event_id, second.current_update)]


def test_destination_disabled_after_403_and_new_webhook_can_retry(
    tmp_path: Path,
) -> None:
    clock = SteppingClock(NOW)
    with SQLiteStorage(tmp_path / "disabled.db") as storage:
        storage.initialize()
        event = _seed(storage)
        old = _manager(
            storage,
            clock,
            [],
            [
                SendResult(
                    DeliveryOutcome.PERMANENT,
                    safe_reason="HTTP_403",
                    disable_destination=True,
                )
            ],
        )
        assert old.process_one()
        assert _state(storage, event)["state"] == "PERMANENT_FAILURE"
        assert not old.process_one()
        calls: list[str] = []

        def send_new(_: Event, __: ResearchReport, identity: str) -> SendResult:
            calls.append(identity)
            return SendResult(DeliveryOutcome.ACKNOWLEDGED, message_id="888")

        new = DeliveryManager(
            storage,
            clock=clock,
            webhook_url="https://discord.com/api/webhooks/12345/different-token",
            sender=send_new,
        )
        assert new.process_one()
        assert _state(storage, event)["state"] == "SUCCEEDED"
        assert calls == [delivery_id(event.event_id, 1)]


def test_unrepresentable_provider_wait_holds_destination(tmp_path: Path) -> None:
    clock = SteppingClock(NOW)
    calls: list[str] = []
    with SQLiteStorage(tmp_path / "huge-wait.db") as storage:
        storage.initialize()
        _seed(storage)
        manager = _manager(
            storage,
            clock,
            calls,
            [
                SendResult(
                    DeliveryOutcome.DEFINITE_RETRY,
                    safe_reason="HTTP_429",
                    wait_seconds=Decimal("1e999"),
                )
            ],
        )
        assert manager.process_one()
        row = storage._connection.execute(
            "SELECT disabled_reason FROM notification_destination_state"
        ).fetchone()
        assert row is not None and row[0] == "UNREPRESENTABLE_WAIT"
        clock.advance_to(NOW + timedelta(days=1))
        assert not manager.process_one()
    assert len(calls) == 1
