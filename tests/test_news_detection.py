"""Tests for deterministic news filtering."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from investment_assistant.detection import detect_news_signal
from investment_assistant.models import NewsRecord

BASE_RECORD = NewsRecord(
    symbol="ACME",
    headline="Acme reports a routine business update",
    summary="No material change was announced.",
    published_at=datetime(2026, 1, 15, 15, 45, tzinfo=UTC),
    source="Fixture Wire",
)


@pytest.mark.parametrize(
    ("headline", "matched_phrase"),
    [
        ("Acme announces guidance cut", "guidance cut"),
        ("Acme reports EARNINGS MISS", "earnings miss"),
        ("Regulator opens investigation into Acme", "investigation"),
        ("Acme begins product recall", "product recall"),
    ],
)
def test_detects_fixed_phrase_in_headline(
    headline: str,
    matched_phrase: str,
) -> None:
    record = replace(BASE_RECORD, headline=headline)

    signal = detect_news_signal(record, tracked_symbol=" acme ")

    assert signal is not None
    assert signal.matched_phrase == matched_phrase
    assert signal.symbol == "ACME"
    assert signal.occurred_at == record.published_at
    assert signal.headline == headline
    assert signal.source == "Fixture Wire"


def test_detects_case_insensitive_phrase_in_summary() -> None:
    record = replace(
        BASE_RECORD,
        summary="The company started a PRODUCT RECALL in one region.",
    )

    signal = detect_news_signal(record, tracked_symbol="ACME")

    assert signal is not None
    assert signal.matched_phrase == "product recall"


def test_rejects_news_without_fixed_phrase() -> None:
    assert detect_news_signal(BASE_RECORD, tracked_symbol="ACME") is None


def test_rejects_news_for_different_symbol() -> None:
    record = replace(BASE_RECORD, headline="Acme announces guidance cut")

    assert detect_news_signal(record, tracked_symbol="OTHER") is None


def test_rejects_blank_tracked_symbol() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        detect_news_signal(BASE_RECORD, tracked_symbol="   ")
