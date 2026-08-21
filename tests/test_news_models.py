"""Tests for live news article and classification models."""

from datetime import UTC, datetime, timedelta

import pytest

from investment_assistant.models import (
    MAX_NEWS_HEADLINE_CHARS,
    ClassificationStatus,
    NewsArticle,
    NewsCategory,
    NewsClassification,
    NewsDirection,
    SignalImportance,
)

CREATED_AT = datetime(2026, 8, 21, 14, 0, tzinfo=UTC)


def _article(**overrides: object) -> NewsArticle:
    values: dict[str, object] = {
        "provider": "alpaca",
        "provider_article_id": "24843171",
        "symbols": ("TSLA",),
        "headline": "Tesla reports record deliveries",
        "summary": "Vehicle deliveries rose.",
        "content": "Tesla said quarterly deliveries increased.",
        "url": "https://www.benzinga.com/news/tesla-deliveries",
        "canonical_url": "https://www.benzinga.com/news/tesla-deliveries",
        "source": "benzinga",
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT + timedelta(seconds=1),
        "retrieved_at": CREATED_AT + timedelta(minutes=1),
        "content_fingerprint": "a" * 64,
    }
    values.update(overrides)
    return NewsArticle(**values)  # type: ignore[arg-type]


def test_article_identity_is_provider_and_id() -> None:
    article = _article()

    assert article.article_id == "alpaca:24843171"
    assert article.symbols == ("TSLA",)


def test_article_normalizes_tickers_and_rejects_empty_symbols() -> None:
    article = _article(symbols=(" tsla ", "amd"))

    assert article.symbols == ("TSLA", "AMD")
    with pytest.raises(ValueError, match="symbols must not be empty"):
        _article(symbols=())


def test_article_rejects_oversized_headline() -> None:
    with pytest.raises(ValueError, match="headline"):
        _article(headline="T" * (MAX_NEWS_HEADLINE_CHARS + 1))


def test_successful_classification_requires_complete_fields() -> None:
    with pytest.raises(ValueError, match="complete result"):
        NewsClassification(
            article_id="alpaca:1",
            ticker="TSLA",
            prompt_version="news-classifier-v1",
            model_version="gpt-5.4-nano-2026-03-17",
            status=ClassificationStatus.SUCCEEDED,
            attempted_at=CREATED_AT,
            relevant=True,
        )


def test_failed_classification_requires_safe_error() -> None:
    with pytest.raises(ValueError, match="safe_error"):
        NewsClassification(
            article_id="alpaca:1",
            ticker="TSLA",
            prompt_version="news-classifier-v1",
            model_version="gpt-5.4-nano-2026-03-17",
            status=ClassificationStatus.FAILED,
            attempted_at=CREATED_AT,
        )


def test_confidence_must_be_in_unit_interval() -> None:
    with pytest.raises(ValueError, match="confidence"):
        NewsClassification(
            article_id="alpaca:1",
            ticker="TSLA",
            prompt_version="news-classifier-v1",
            model_version="gpt-5.4-nano-2026-03-17",
            status=ClassificationStatus.SUCCEEDED,
            attempted_at=CREATED_AT,
            relevant=True,
            category=NewsCategory.EARNINGS,
            significant=True,
            direction=NewsDirection.UP,
            importance=SignalImportance.HIGH,
            confidence=1.2,
            rationale="Earnings beat with raised outlook.",
        )
