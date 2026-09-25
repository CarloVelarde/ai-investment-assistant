"""Narrow local commands for held Discord deliveries."""

import argparse
import json
from collections.abc import Sequence

from investment_assistant.clock import Clock, SystemClock
from investment_assistant.config import Settings
from investment_assistant.delivery import (
    DatabaseOwner,
    DeliveryManager,
    DeliveryState,
    destination_fingerprint,
    read_only_deliveries,
)
from investment_assistant.discord_notify import DiscordNotifier
from investment_assistant.storage import SQLiteStorage


def run_notifications(
    settings: Settings, args: Sequence[str], *, clock: Clock | None = None
) -> int:
    parser = argparse.ArgumentParser(prog="investment_assistant notifications")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    retry = commands.add_parser("retry")
    retry.add_argument("--delivery-id", required=True)
    retry.add_argument("--accept-duplicate-risk", action="store_true", required=True)
    confirm = commands.add_parser("confirm")
    confirm.add_argument("--delivery-id", required=True)
    confirm.add_argument("--message-id", required=True)
    parsed = parser.parse_args(args)
    current_clock = clock or SystemClock()
    if parsed.command == "list":
        webhook = settings.discord_webhook_url.get_secret_value()
        fingerprint = destination_fingerprint(webhook) if webhook else None
        for record in read_only_deliveries(settings.database_path):
            retry_at = record.next_attempt_at or record.resend_not_before
            uncertain_recovery = None
            if record.state is DeliveryState.UNCERTAIN:
                if record.uncertain_resend_used:
                    uncertain_recovery = "operator_review"
                elif record.destination_fingerprint != fingerprint:
                    uncertain_recovery = "restore_destination"
                else:
                    uncertain_recovery = "awaiting_resend"
            print(
                json.dumps(
                    {
                        "delivery_id": record.delivery_id,
                        "event_id": record.event_id,
                        "event_update": record.event_update,
                        "destination": record.destination,
                        "state": record.state.value,
                        "created_at": record.created_at.isoformat(),
                        "updated_at": record.updated_at.isoformat(),
                        "safe_reason": record.safe_reason,
                        "retry_at": retry_at.isoformat() if retry_at else None,
                        "message_id": record.message_id,
                        "manual_authorized": record.manual_authorized,
                        "uncertain_recovery": uncertain_recovery,
                    }
                )
            )
        return 0
    webhook = settings.discord_webhook_url.get_secret_value()
    if not webhook:
        print("A matching Discord webhook must be configured.")
        return 1
    with (
        DatabaseOwner(settings.database_path),
        SQLiteStorage(settings.database_path) as storage,
    ):
        storage.initialize(now=current_clock.now())
        notifier = DiscordNotifier(webhook)
        manager = DeliveryManager(
            storage,
            clock=current_clock,
            webhook_url=webhook,
            sender=notifier.send,
            lookup=notifier.confirm,
        )
        if parsed.command == "retry":
            accepted = manager.authorize_retry(parsed.delivery_id)
            print(
                "One retry authorized for live processing."
                if accepted
                else "Retry unavailable for this delivery."
            )
        else:
            accepted = manager.confirm_receipt(parsed.delivery_id, parsed.message_id)
            print("Receipt confirmed." if accepted else "Receipt not confirmed.")
        return 0 if accepted else 1
