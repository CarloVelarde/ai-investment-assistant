"""Tests for structured news classification at the adapter boundary."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from investment_assistant.clock import FixedClock
from investment_assistant.models import (
    ClassificationStatus,
    NewsArticle,
    NewsCategory,
    NewsClassification,
    NewsDirection,
    SignalImportance,
)
from investment_assistant.news_classifier import (
    CLASSIFIER_CONFIDENCE_FLOOR,
    CLASSIFIER_MODEL,
    CLASSIFIER_PROMPT_VERSION,
    ClassifierHttpResponse,
    FakeNewsClassifier,
    NewsClassifierError,
    OpenAINewsClassifier,
    classification_from_model_output,
    is_qualifying_classification,
)

NOW = datetime(2026, 8, 21, 16, 0, tzinfo=UTC)
API_KEY = "test-openai-key-do-not-log"


def _article() -> NewsArticle:
    return NewsArticle(
        provider="alpaca",
        provider_article_id="100",
        symbols=("TSLA",),
        headline="Tesla beats earnings estimates",
        summary="EPS and revenue topped forecasts.",
        content="Tesla reported a quarterly earnings beat.",
        url="https://www.benzinga.com/news/tesla-beat",
        canonical_url="https://www.benzinga.com/news/tesla-beat",
        source="benzinga",
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(hours=1),
        retrieved_at=NOW,
        content_fingerprint="b" * 64,
    )


def _valid_output(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "relevant": True,
        "category": "EARNINGS",
        "significant": True,
        "direction": "UP",
        "importance": "HIGH",
        "confidence": 0.91,
        "rationale": "Reported earnings beat with raised delivery outlook.",
    }
    payload.update(overrides)
    return payload


class ScriptedClassifierHttp:
    def __init__(self, responses: list[ClassifierHttpResponse]) -> None:
        self._responses = list(responses)
        self.payloads: list[dict[str, object]] = []

    def post_responses(self, payload: Mapping[str, object]) -> ClassifierHttpResponse:
        self.payloads.append(dict(payload))
        if not self._responses:
            raise AssertionError("unexpected classifier request")
        return self._responses.pop(0)


def _classifier(http: ScriptedClassifierHttp) -> OpenAINewsClassifier:
    return OpenAINewsClassifier(
        http=http,
        clock=FixedClock(NOW),
        api_key=API_KEY,
    )


def test_strict_schema_accepts_significant_positive_negative_and_unclear() -> None:
    cases = (
        ("UP", NewsDirection.UP),
        ("DOWN", NewsDirection.DOWN),
        ("UNCLEAR", NewsDirection.UNCLEAR),
    )
    for raw, expected in cases:
        result = classification_from_model_output(
            _valid_output(direction=raw),
            article_id="alpaca:100",
            ticker="TSLA",
            prompt_version=CLASSIFIER_PROMPT_VERSION,
            model_version=CLASSIFIER_MODEL,
            attempted_at=NOW,
        )
        assert result.direction is expected
        assert is_qualifying_classification(result) is True


@pytest.mark.parametrize(
    ("output", "match"),
    [
        (_valid_output(category="RUMOR"), "category"),
        (_valid_output(confidence=1.4), "confidence"),
        (_valid_output(confidence=True), "confidence"),
        (_valid_output(relevant="yes"), "relevant"),
        (["not-an-object"], "must be an object"),
        (_valid_output(rationale=""), "rationale"),
        (_valid_output(rationale="x" * 501), "too long"),
    ],
)
def test_malformed_output_is_rejected(output: object, match: str) -> None:
    with pytest.raises(NewsClassifierError, match=match):
        classification_from_model_output(
            output,
            article_id="alpaca:100",
            ticker="TSLA",
            prompt_version=CLASSIFIER_PROMPT_VERSION,
            model_version=CLASSIFIER_MODEL,
            attempted_at=NOW,
        )


def test_adapter_sets_store_false_no_tools_and_pinned_model() -> None:
    http = ScriptedClassifierHttp(
        [
            ClassifierHttpResponse(
                status_code=200,
                body={"status": "completed", "output_text": _json(_valid_output())},
            )
        ]
    )

    result = _classifier(http).classify(_article(), "tsla")

    assert result.status is ClassificationStatus.SUCCEEDED
    payload = http.payloads[0]
    assert payload["model"] == CLASSIFIER_MODEL
    assert payload["store"] is False
    assert "tools" not in payload
    assert result.model_version == CLASSIFIER_MODEL
    assert result.prompt_version == CLASSIFIER_PROMPT_VERSION
    assert API_KEY not in str(payload)


def test_refusal_and_transport_failure_are_retryable_and_secret_safe() -> None:
    refused = _classifier(
        ScriptedClassifierHttp(
            [
                ClassifierHttpResponse(
                    status_code=200,
                    body={"status": "completed", "output": [{"type": "refusal"}]},
                )
            ]
        )
    ).classify(_article(), "TSLA")
    failed = _classifier(
        ScriptedClassifierHttp(
            [
                ClassifierHttpResponse(
                    status_code=500,
                    body={"error": {"message": API_KEY}},
                )
            ]
        )
    ).classify(_article(), "TSLA")

    assert refused.status is ClassificationStatus.FAILED
    assert failed.status is ClassificationStatus.FAILED
    assert refused.safe_error is not None
    assert failed.safe_error is not None
    assert API_KEY not in refused.safe_error
    assert API_KEY not in failed.safe_error
    assert is_qualifying_classification(refused) is False


def test_low_confidence_is_not_qualifying() -> None:
    result = classification_from_model_output(
        _valid_output(confidence=0.69),
        article_id="alpaca:100",
        ticker="TSLA",
        prompt_version=CLASSIFIER_PROMPT_VERSION,
        model_version=CLASSIFIER_MODEL,
        attempted_at=NOW,
    )

    assert result.status is ClassificationStatus.SUCCEEDED
    assert result.confidence == 0.69
    assert result.confidence < CLASSIFIER_CONFIDENCE_FLOOR
    assert is_qualifying_classification(result) is False


def test_fake_classifier_records_calls() -> None:
    article = _article()
    scripted = NewsClassification(
        article_id=article.article_id,
        ticker="TSLA",
        prompt_version=CLASSIFIER_PROMPT_VERSION,
        model_version=CLASSIFIER_MODEL,
        status=ClassificationStatus.SUCCEEDED,
        attempted_at=NOW,
        relevant=True,
        category=NewsCategory.EARNINGS,
        significant=True,
        direction=NewsDirection.UP,
        importance=SignalImportance.HIGH,
        confidence=0.9,
        rationale="Earnings beat.",
    )
    fake = FakeNewsClassifier({("100", "TSLA"): scripted})

    assert fake.classify(article, "TSLA") == scripted
    assert fake.calls == [("100", "TSLA")]


def _json(payload: dict[str, object]) -> str:
    import json

    return json.dumps(payload)
