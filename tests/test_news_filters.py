"""Tests for deterministic news eligibility, duplicates, and call budgets."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from investment_assistant.clock import FixedClock
from investment_assistant.event_manager import EventManager
from investment_assistant.models import (
    ClassificationStatus,
    NewsArticle,
    NewsCategory,
    NewsClassification,
    NewsDirection,
    SignalImportance,
)
from investment_assistant.news import FakeNewsProvider, NewsPage
from investment_assistant.news_classifier import (
    CLASSIFIER_MODEL,
    CLASSIFIER_PROMPT_VERSION,
    FakeNewsClassifier,
)
from investment_assistant.news_ingest import (
    CLASSIFIER_CALLS_PER_PASS,
    CLASSIFIER_CALLS_PER_UTC_DAY,
    NEWS_STARTUP_LOOKBACK,
    poll_and_classify_news,
)
from investment_assistant.storage import SQLiteStorage

NOW = datetime(2026, 8, 21, 16, 0, tzinfo=UTC)


def _article(
    article_id: str,
    *,
    symbols: tuple[str, ...] = ("TSLA",),
    created_at: datetime | None = None,
    url: str | None = None,
    headline: str = "Tesla announces a material update",
) -> NewsArticle:
    created = created_at or (NOW - timedelta(hours=2))
    path = url or f"https://www.benzinga.com/news/{article_id}"
    return NewsArticle(
        provider="alpaca",
        provider_article_id=article_id,
        symbols=symbols,
        headline=headline,
        summary="Company update.",
        content="Details of the company update.",
        url=path,
        canonical_url=path.split("?")[0],
        source="benzinga",
        created_at=created,
        updated_at=created,
        retrieved_at=NOW,
        content_fingerprint=article_id.zfill(64)[:64],
    )


def _success(
    article: NewsArticle, ticker: str, **overrides: object
) -> NewsClassification:
    values: dict[str, object] = {
        "article_id": article.article_id,
        "ticker": ticker,
        "prompt_version": CLASSIFIER_PROMPT_VERSION,
        "model_version": CLASSIFIER_MODEL,
        "status": ClassificationStatus.SUCCEEDED,
        "attempted_at": NOW,
        "relevant": True,
        "category": NewsCategory.EARNINGS,
        "significant": True,
        "direction": NewsDirection.UP,
        "importance": SignalImportance.HIGH,
        "confidence": 0.9,
        "rationale": "Material earnings development.",
    }
    values.update(overrides)
    return NewsClassification(**values)  # type: ignore[arg-type]


def _run(
    tmp_path: Path,
    *,
    articles: tuple[NewsArticle, ...],
    watchlist: tuple[str, ...] = ("TSLA",),
    classifier: FakeNewsClassifier,
    high_water: datetime | None = None,
) -> tuple[SQLiteStorage, FakeNewsClassifier]:
    database = tmp_path / "news.sqlite3"
    storage = SQLiteStorage(database)
    storage.initialize()
    if high_water is not None:
        storage.save_news_high_water("alpaca", high_water, updated_at=NOW)
    manager = EventManager(storage, clock=FixedClock(NOW))
    provider = FakeNewsProvider((NewsPage(articles=articles),))
    poll_and_classify_news(
        storage=storage,
        manager=manager,
        provider=provider,
        classifier=classifier,
        watchlist=watchlist,
        clock=FixedClock(NOW),
    )
    return storage, classifier


def test_comparison_only_spy_does_not_create_classifier_calls(tmp_path: Path) -> None:
    article = _article("1", symbols=("SPY", "TSLA"))
    classifier = FakeNewsClassifier(
        {
            ("1", "TSLA"): _success(article, "TSLA"),
            ("1", "SPY"): _success(article, "SPY"),
        }
    )

    storage, classifier = _run(
        tmp_path,
        articles=(article,),
        watchlist=("TSLA",),
        classifier=classifier,
    )

    assert classifier.calls == [("1", "TSLA")]
    assert (
        storage.get_news_classification(
            article.article_id,
            "SPY",
            prompt_version=CLASSIFIER_PROMPT_VERSION,
            model_version=CLASSIFIER_MODEL,
        )
        is None
    )


def test_explicit_spy_can_be_classified(tmp_path: Path) -> None:
    article = _article("2", symbols=("SPY",))
    classifier = FakeNewsClassifier({("2", "SPY"): _success(article, "SPY")})

    _run(
        tmp_path,
        articles=(article,),
        watchlist=("SPY", "TSLA"),
        classifier=classifier,
    )

    assert classifier.calls == [("2", "SPY")]


def test_filtered_candidates_make_zero_classifier_calls(tmp_path: Path) -> None:
    stale = _article(
        "old",
        created_at=NOW - NEWS_STARTUP_LOOKBACK - timedelta(hours=1),
    )
    duplicate_first = _article("a", url="https://www.benzinga.com/news/same")
    duplicate_second = _article("b", url="https://www.benzinga.com/news/same?utm=1")
    classifier = FakeNewsClassifier(
        {
            ("old", "TSLA"): _success(stale, "TSLA"),
            ("a", "TSLA"): _success(duplicate_first, "TSLA"),
            ("b", "TSLA"): _success(duplicate_second, "TSLA"),
        }
    )

    storage, classifier = _run(
        tmp_path,
        articles=(stale, duplicate_first, duplicate_second),
        classifier=classifier,
    )

    assert classifier.calls == [("a", "TSLA")]
    assert storage.get_news_article(stale.article_id) is not None
    assert (
        storage.get_news_classification(
            stale.article_id,
            "TSLA",
            prompt_version=CLASSIFIER_PROMPT_VERSION,
            model_version=CLASSIFIER_MODEL,
        )
        is None
    )
    duplicate_row = storage.get_news_classification(
        duplicate_second.article_id,
        "TSLA",
        prompt_version=CLASSIFIER_PROMPT_VERSION,
        model_version=CLASSIFIER_MODEL,
    )
    assert duplicate_row is not None
    assert duplicate_row.status is ClassificationStatus.FILTERED


def test_pass_budget_defers_without_calling_or_rejecting(tmp_path: Path) -> None:
    articles = tuple(
        _article(str(index)) for index in range(CLASSIFIER_CALLS_PER_PASS + 3)
    )
    results = {
        (article.provider_article_id, "TSLA"): _success(article, "TSLA")
        for article in articles
    }
    classifier = FakeNewsClassifier(results)

    storage, classifier = _run(tmp_path, articles=articles, classifier=classifier)

    assert len(classifier.calls) == CLASSIFIER_CALLS_PER_PASS
    statuses = []
    for article in articles:
        row = storage.get_news_classification(
            article.article_id,
            "TSLA",
            prompt_version=CLASSIFIER_PROMPT_VERSION,
            model_version=CLASSIFIER_MODEL,
        )
        assert row is not None
        statuses.append(row.status)
    assert statuses.count(ClassificationStatus.SUCCEEDED) == CLASSIFIER_CALLS_PER_PASS
    assert statuses.count(ClassificationStatus.DEFERRED) == 3
    events = storage.list_events()
    assert len(events) == 1
    assert len(storage.list_signals(events[0].event_id)) == CLASSIFIER_CALLS_PER_PASS
    assert storage.classifier_call_count(NOW.date().isoformat()) == (
        CLASSIFIER_CALLS_PER_PASS
    )


def test_daily_budget_defers_remaining_candidates(tmp_path: Path) -> None:
    database = tmp_path / "day-budget.sqlite3"
    storage = SQLiteStorage(database)
    storage.initialize()
    utc_day = NOW.date().isoformat()
    for _ in range(CLASSIFIER_CALLS_PER_UTC_DAY):
        storage.record_classifier_call(utc_day)
    article = _article("late")
    classifier = FakeNewsClassifier({("late", "TSLA"): _success(article, "TSLA")})
    manager = EventManager(storage, clock=FixedClock(NOW))

    result = poll_and_classify_news(
        storage=storage,
        manager=manager,
        provider=FakeNewsProvider((NewsPage(articles=(article,)),)),
        classifier=classifier,
        watchlist=("TSLA",),
        clock=FixedClock(NOW),
    )

    assert classifier.calls == []
    assert result.deferred == 1
    saved = storage.get_news_classification(
        article.article_id,
        "TSLA",
        prompt_version=CLASSIFIER_PROMPT_VERSION,
        model_version=CLASSIFIER_MODEL,
    )
    assert saved is not None
    assert saved.status is ClassificationStatus.DEFERRED
