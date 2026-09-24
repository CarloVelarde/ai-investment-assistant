"""Durable count and estimated-cost admission without provider calls."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from investment_assistant.config import Settings
from investment_assistant.model_budget import (
    CLASSIFIER_MODEL,
    RESEARCH_ALLOWANCE,
    RESEARCH_MODEL,
    extract_usage,
    token_charge,
)
from investment_assistant.models import (
    Event,
    EventStatus,
    MarketWindow,
    ResearchAttempt,
    SignalDirection,
    SignalImportance,
)
from investment_assistant.storage import SQLiteStorage

NOW = datetime(2026, 9, 24, 23, 55, tzinfo=UTC)


def _event(storage: SQLiteStorage, number: int) -> Event:
    event = Event(
        event_id=f"event:budget-{number}",
        ticker="TEST",
        direction=SignalDirection.UP,
        category=None,
        importance=SignalImportance.MODERATE,
        market_windows=(MarketWindow.ONE_HOUR,),
        current_update=1,
        status=EventStatus.QUEUED,
        created_at=NOW,
        updated_at=NOW,
    )
    storage.save_event(event)
    return event


def _reserve_research(
    storage: SQLiteStorage, event: Event, now: datetime, cap: int = 2_000_000
) -> ResearchAttempt | None:
    return storage.reserve_research_attempt(
        event.event_id,
        1,
        now=now,
        model_version=RESEARCH_MODEL,
        prompt_version="research-v1",
        has_api_key=True,
        budget_microdollars=cap,
    )


def test_research_reserves_before_io_and_releases_unstarted_slots(
    tmp_path: Path,
) -> None:
    path = tmp_path / "budget.db"
    with SQLiteStorage(path) as storage:
        storage.initialize()
        first, second = _event(storage, 1), _event(storage, 2)
        attempt = _reserve_research(storage, first, NOW)
        assert attempt is not None
        assert storage.model_budget_used(NOW.date().isoformat()) == RESEARCH_ALLOWANCE
        assert _reserve_research(storage, second, NOW) is None
        number = storage.start_research_request(attempt.attempt_id, search_slots=3)
        storage.settle_model_request(
            attempt.attempt_id,
            number,
            usage=(100, 200),
            search_calls=1,
        )
        storage.finish_model_run(attempt.attempt_id)
        expected = token_charge(RESEARCH_MODEL, 100, 200) + 10_000
        assert storage.model_budget_used(NOW.date().isoformat()) == expected
        assert _reserve_research(storage, second, NOW) is not None


def test_interrupted_requests_keep_charge_and_utc_rollover(tmp_path: Path) -> None:
    path = tmp_path / "interrupted.db"
    with SQLiteStorage(path) as storage:
        storage.initialize()
        first = _event(storage, 1)
        attempt = _reserve_research(storage, first, NOW)
        assert attempt is not None
        storage.start_research_request(attempt.attempt_id, search_slots=2)
    with SQLiteStorage(path) as storage:
        storage.initialize(now=NOW + timedelta(minutes=10))
        assert storage.model_budget_used(NOW.date().isoformat()) == 338_000
        assert (
            storage.model_budget_used((NOW + timedelta(minutes=10)).date().isoformat())
            == 0
        )
        second = _event(storage, 2)
        assert (
            _reserve_research(storage, second, NOW + timedelta(minutes=10)) is not None
        )


def test_missing_search_count_keeps_search_allowance_and_overrun_is_visible(
    tmp_path: Path,
) -> None:
    with SQLiteStorage(tmp_path / "search-budget.db") as storage:
        storage.initialize()
        event = _event(storage, 1)
        attempt = _reserve_research(storage, event, NOW)
        assert attempt is not None
        number = storage.start_research_request(attempt.attempt_id, search_slots=3)
        storage.settle_model_request(attempt.attempt_id, number, usage=(100, 10))
        storage.finish_model_run(attempt.attempt_id)
        assert storage.model_budget_used(NOW.date().isoformat()) == (
            token_charge(RESEARCH_MODEL, 100, 10) + 30_000
        )
        second = _event(storage, 2)
        later = _reserve_research(storage, second, NOW)
        assert later is not None
        number = storage.start_research_request(later.attempt_id, search_slots=1)
        storage.settle_model_request(
            later.attempt_id,
            number,
            usage=(100, 10),
            search_calls=2,
        )
        storage.finish_model_run(later.attempt_id)
        assert storage.model_budget_overrun(NOW.date().isoformat())


def test_classifier_count_unknown_usage_cap_reduction_and_overrun(
    tmp_path: Path,
) -> None:
    path = tmp_path / "classifier.db"
    with SQLiteStorage(path) as storage:
        storage.initialize()
        owner = storage.reserve_classifier_call(
            now=NOW,
            model_version=CLASSIFIER_MODEL,
            daily_calls=1,
            budget_microdollars=100_000,
        )
        assert owner is not None
        assert storage.classifier_call_count(NOW.date().isoformat()) == 1
        assert (
            storage.reserve_classifier_call(
                now=NOW,
                model_version=CLASSIFIER_MODEL,
                daily_calls=0,
                budget_microdollars=10_000_000,
            )
            is None
        )
        storage.finish_model_run(owner)
        assert storage.model_budget_used(NOW.date().isoformat()) == 82_500
        assert (
            storage.reserve_classifier_call(
                now=NOW + timedelta(minutes=10),
                model_version=CLASSIFIER_MODEL,
                daily_calls=1,
                budget_microdollars=100_000,
            )
            is not None
        )
        later = NOW + timedelta(minutes=10)
        assert storage.classifier_call_count(later.date().isoformat()) == 1

    with SQLiteStorage(tmp_path / "overrun.db") as storage:
        storage.initialize()
        owner = storage.reserve_classifier_call(
            now=NOW,
            model_version=CLASSIFIER_MODEL,
            daily_calls=100,
            budget_microdollars=2_000_000,
        )
        assert owner is not None
        storage.settle_model_request(owner, 1, usage=(500_000, 3_000))
        storage.finish_model_run(owner)
        assert storage.model_budget_overrun(NOW.date().isoformat())
        assert (
            storage.reserve_classifier_call(
                now=NOW,
                model_version=CLASSIFIER_MODEL,
                daily_calls=100,
                budget_microdollars=10_000_000,
            )
            is None
        )


def test_unknown_price_and_settings_are_rejected(tmp_path: Path) -> None:
    with SQLiteStorage(tmp_path / "unknown.db") as storage:
        storage.initialize()
        assert (
            storage.reserve_classifier_call(
                now=NOW,
                model_version="unpriced-model",
                daily_calls=100,
                budget_microdollars=2_000_000,
            )
            is None
        )
        assert storage.classifier_call_count(NOW.date().isoformat()) == 0
    assert extract_usage({"usage": {"input_tokens": 10, "output_tokens": 2}}) == (10, 2)
    assert extract_usage({"usage": {"input_tokens": True, "output_tokens": 2}}) is None
    for values in (
        {"research_starts_per_day": 21},
        {"classifier_calls_per_day": -1},
        {"classifier_calls_per_pass": 21},
        {"daily_model_budget_usd": "NaN"},
        {"daily_model_budget_usd": "1.001"},
        {"daily_model_budget_usd": "10.01"},
    ):
        with pytest.raises(ValidationError):
            Settings(**values)
