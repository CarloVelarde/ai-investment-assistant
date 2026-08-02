"""Tests for offline fixture loading and normalization."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from investment_assistant.fixture_readers import (
    load_market_fixture,
    load_news_fixture,
)

FIXTURE_DIR = (
    Path(__file__).parents[1]
    / "src"
    / "investment_assistant"
    / "fixtures"
    / "offline_walking_skeleton"
)


@pytest.mark.parametrize("scenario", ["triggering", "non_triggering"])
def test_loads_fixture_pair(scenario: str) -> None:
    market = load_market_fixture(FIXTURE_DIR / f"{scenario}_market.json")
    news = load_news_fixture(FIXTURE_DIR / f"{scenario}_news.json")

    assert market.symbol == "ACME"
    assert news.symbol == "ACME"
    assert market.occurred_at.tzinfo is UTC
    assert news.published_at.tzinfo is UTC
    assert market.provider == "fixture"
    assert market.feed == "offline-demo"
    assert news.source == "Fixture Wire"


def test_normalizes_market_fixture() -> None:
    market = load_market_fixture(FIXTURE_DIR / "triggering_market.json")

    assert market.latest_price == Decimal("94.0")
    assert market.previous_close == Decimal("100.0")
    assert market.current_volume == Decimal("1600000")
    assert market.average_volume == Decimal("1000000")
    assert market.occurred_at == datetime(2026, 1, 15, 15, 30, tzinfo=UTC)


def test_normalizes_news_fixture() -> None:
    news = load_news_fixture(FIXTURE_DIR / "triggering_news.json")

    assert news.headline == "Acme announces guidance cut"
    assert news.summary == "The fictional company lowered its full-year outlook."
    assert news.published_at == datetime(2026, 1, 15, 15, 45, tzinfo=UTC)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("latest_price", 0),
        ("previous_close", 0),
        ("current_volume", -1),
        ("average_volume", 0),
        ("observed_at", "2026-01-15T09:30:00"),
        ("ticker", "   "),
    ],
)
def test_rejects_invalid_market_fixture(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "ticker": "ACME",
        "latest_price": 94.0,
        "previous_close": 100.0,
        "current_volume": 1600000,
        "average_volume": 1000000,
        "observed_at": "2026-01-15T09:30:00-06:00",
        "provider": "fixture",
        "feed": "offline-demo",
    }
    payload[field] = value
    path = tmp_path / "market.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_market_fixture(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("published_at", "2026-01-15T15:45:00"),
        ("ticker", "   "),
        ("headline", "   "),
        ("source", "   "),
    ],
)
def test_rejects_invalid_news_fixture(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "ticker": "ACME",
        "headline": "Acme announces guidance cut",
        "summary": "The fictional company lowered its full-year outlook.",
        "published_at": "2026-01-15T15:45:00Z",
        "source": "Fixture Wire",
    }
    payload[field] = value
    path = tmp_path / "news.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_news_fixture(path)
