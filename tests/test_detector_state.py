"""Tests for detector cross / escalate / quiet / rearm state."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from investment_assistant.market_detection import (
    CrossingDecision,
    decide_crossing,
    evaluate_and_store_crossing,
)
from investment_assistant.market_metrics import RULE_MULTI_DAY_MOVE, thresholds_for
from investment_assistant.models import (
    DetectorState,
    MarketWindow,
    SignalDirection,
    SignalImportance,
)
from investment_assistant.storage import SQLiteStorage

NOW = datetime(2026, 2, 3, 21, 0, tzinfo=UTC)
LATER = datetime(2026, 2, 4, 21, 0, tzinfo=UTC)
TABLE = thresholds_for(RULE_MULTI_DAY_MOVE, MarketWindow.FIVE_DAYS)


def _decision(
    magnitude: Decimal,
    previous: DetectorState | None,
    *,
    now: datetime = NOW,
) -> CrossingDecision:
    return decide_crossing(
        ticker="TSLA",
        rule=RULE_MULTI_DAY_MOVE,
        window=MarketWindow.FIVE_DAYS,
        direction=SignalDirection.DOWN,
        magnitude=magnitude,
        candidate_importance=TABLE.importance_for(magnitude),
        previous=previous,
        now=now,
    )


def test_first_moderate_cross_emits_and_arms_state() -> None:
    decision = _decision(Decimal("0.05"), previous=None)

    assert decision.emit_importance is SignalImportance.MODERATE
    assert decision.next_state.last_emitted_importance is SignalImportance.MODERATE


def test_same_or_lower_importance_stays_quiet() -> None:
    first = _decision(Decimal("0.08"), previous=None)
    same = _decision(Decimal("0.08"), previous=first.next_state, now=LATER)
    lower = _decision(Decimal("0.05"), previous=first.next_state, now=LATER)

    assert first.emit_importance is SignalImportance.HIGH
    assert same.emit_importance is None
    assert lower.emit_importance is None
    assert same.next_state.last_emitted_importance is SignalImportance.HIGH
    assert lower.next_state.last_emitted_importance is SignalImportance.HIGH


def test_higher_importance_escalates() -> None:
    first = _decision(Decimal("0.05"), previous=None)
    escalated = _decision(Decimal("0.12"), previous=first.next_state, now=LATER)

    assert first.emit_importance is SignalImportance.MODERATE
    assert escalated.emit_importance is SignalImportance.CRITICAL
    assert escalated.next_state.last_emitted_importance is SignalImportance.CRITICAL


def test_recovery_between_moderate_and_rearm_does_not_clear() -> None:
    stressed = _decision(Decimal("0.08"), previous=None)
    cooling = _decision(Decimal("0.03"), previous=stressed.next_state, now=LATER)

    assert cooling.emit_importance is None
    assert cooling.next_state.last_emitted_importance is SignalImportance.HIGH


def test_rearm_clears_state_and_allows_a_new_cross() -> None:
    stressed = _decision(Decimal("0.08"), previous=None)
    rearmed = _decision(Decimal("0.02"), previous=stressed.next_state, now=LATER)
    crossed_again = _decision(Decimal("0.05"), previous=rearmed.next_state)

    assert rearmed.emit_importance is None
    assert rearmed.next_state.last_emitted_importance is None
    assert crossed_again.emit_importance is SignalImportance.MODERATE


def test_state_survives_reopen_and_blocks_duplicate_fire(tmp_path: Path) -> None:
    database_path = tmp_path / "detector-state.sqlite3"

    with SQLiteStorage(database_path) as storage:
        storage.initialize()
        first = evaluate_and_store_crossing(
            storage,
            ticker="TSLA",
            rule=RULE_MULTI_DAY_MOVE,
            window=MarketWindow.FIVE_DAYS,
            direction=SignalDirection.DOWN,
            magnitude=Decimal("0.08"),
            now=NOW,
        )
        assert first.emit_importance is SignalImportance.HIGH

    with SQLiteStorage(database_path) as reopened:
        reopened.initialize()
        replay = evaluate_and_store_crossing(
            reopened,
            ticker="tsla",
            rule=RULE_MULTI_DAY_MOVE,
            window=MarketWindow.FIVE_DAYS,
            direction=SignalDirection.DOWN,
            magnitude=Decimal("0.08"),
            now=LATER,
        )
        loaded = reopened.get_detector_state(
            "TSLA",
            RULE_MULTI_DAY_MOVE,
            MarketWindow.FIVE_DAYS,
            SignalDirection.DOWN,
        )

    assert replay.emit_importance is None
    assert loaded is not None
    assert loaded.last_emitted_importance is SignalImportance.HIGH
