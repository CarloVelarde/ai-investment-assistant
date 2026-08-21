"""Deterministic news polling, eligibility, classification, and promotion."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from investment_assistant.clock import Clock
from investment_assistant.detection import stable_signal_id
from investment_assistant.event_manager import EventManager
from investment_assistant.models import (
    MAX_SAFE_ERROR_CHARS,
    ClassificationStatus,
    NewsArticle,
    NewsClassification,
    NewsDirection,
    NewsSignal,
    SignalDirection,
    SourceDetails,
)
from investment_assistant.news import (
    ALPACA_NEWS_PROVIDER,
    NEWS_MAX_PAGES_PER_PASS,
    NewsProvider,
    NewsProviderError,
)
from investment_assistant.news_classifier import (
    CLASSIFIER_MODEL,
    CLASSIFIER_PROMPT_VERSION,
    NewsClassifier,
    NewsClassifierError,
    is_qualifying_classification,
)
from investment_assistant.ops_log import watch
from investment_assistant.storage import SQLiteStorage

logger = logging.getLogger(__name__)

NEWS_STARTUP_LOOKBACK = timedelta(hours=72)
NEWS_QUERY_OVERLAP = timedelta(minutes=5)
CLASSIFIER_CALLS_PER_PASS = 20
CLASSIFIER_CALLS_PER_UTC_DAY = 100

_TERMINAL_STATUSES = frozenset(
    {ClassificationStatus.SUCCEEDED, ClassificationStatus.FILTERED}
)


@dataclass(frozen=True, slots=True)
class NewsIngestResult:
    """Observable outcome of one news polling pass."""

    persisted_article_ids: tuple[str, ...] = ()
    classifier_calls: int = 0
    accepted_signal_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    filtered: int = 0
    deferred: int = 0


def poll_and_classify_news(
    *,
    storage: SQLiteStorage,
    manager: EventManager,
    provider: NewsProvider,
    classifier: NewsClassifier | None,
    watchlist: Sequence[str],
    clock: Clock,
    prompt_version: str = CLASSIFIER_PROMPT_VERSION,
    model_version: str = CLASSIFIER_MODEL,
) -> NewsIngestResult:
    """Fetch, persist, filter, classify, and promote news for one pass."""

    symbols = tuple(symbol.strip().upper() for symbol in watchlist if symbol.strip())
    if not symbols:
        raise ValueError("news watchlist must not be empty")
    now = clock.now()
    high_water = storage.get_news_high_water(ALPACA_NEWS_PROVIDER)
    # Alpaca sorts news by updated_at, so the durable cursor is that timestamp.
    start = (
        now - NEWS_STARTUP_LOOKBACK
        if high_water is None
        else min(high_water, now) - NEWS_QUERY_OVERLAP
    )
    persisted: list[str] = []
    diagnostics: list[str] = []
    newest_accepted: datetime | None = None
    page_token: str | None = None
    try:
        for _page_number in range(NEWS_MAX_PAGES_PER_PASS):
            page = provider.fetch_news(
                symbols=symbols,
                start=start,
                end=now,
                page_token=page_token,
            )
            diagnostics.extend(page.diagnostics)
            for article in page.articles:
                storage.save_news_article(article)
                persisted.append(article.article_id)
                if newest_accepted is None or article.updated_at > newest_accepted:
                    newest_accepted = article.updated_at
            if newest_accepted is not None:
                storage.save_news_high_water(
                    ALPACA_NEWS_PROVIDER,
                    newest_accepted,
                    updated_at=now,
                )
            if not page.next_page_token:
                break
            page_token = page.next_page_token
        else:
            diagnostics.append("news page cap reached")
    except NewsProviderError as error:
        reason = _safe_text(str(error))
        diagnostics.append(reason)
        logger.warning("News request failed", extra={"reason": reason})

    promotion = _classify_pending(
        storage=storage,
        manager=manager,
        classifier=classifier,
        watchlist=symbols,
        clock=clock,
        prompt_version=prompt_version,
        model_version=model_version,
    )
    result = NewsIngestResult(
        persisted_article_ids=tuple(persisted),
        classifier_calls=promotion.classifier_calls,
        accepted_signal_ids=promotion.accepted_signal_ids,
        diagnostics=tuple(diagnostics) + promotion.diagnostics,
        filtered=promotion.filtered,
        deferred=promotion.deferred,
    )
    if result.persisted_article_ids or result.accepted_signal_ids or result.diagnostics:
        watch(
            "News poll complete",
            articles=len(result.persisted_article_ids),
            classified=result.classifier_calls,
            accepted=len(result.accepted_signal_ids),
        )
    return result


def _classify_pending(
    *,
    storage: SQLiteStorage,
    manager: EventManager,
    classifier: NewsClassifier | None,
    watchlist: tuple[str, ...],
    clock: Clock,
    prompt_version: str,
    model_version: str,
) -> NewsIngestResult:
    now = clock.now()
    window_start = now - NEWS_STARTUP_LOOKBACK
    utc_day = now.date().isoformat()
    day_calls = storage.classifier_call_count(utc_day)
    pass_calls = 0
    accepted: list[str] = []
    diagnostics: list[str] = []
    filtered = 0
    deferred = 0

    for article in storage.list_news_articles_since(window_start):
        for ticker in _watched_tickers(article, watchlist):
            existing = storage.get_news_classification(
                article.article_id,
                ticker,
                prompt_version=prompt_version,
                model_version=model_version,
            )
            if existing is not None and existing.status in _TERMINAL_STATUSES:
                continue
            reason = _filter_reason(
                article,
                ticker,
                storage=storage,
                window_start=window_start,
                prompt_version=prompt_version,
                model_version=model_version,
            )
            if reason is not None:
                _persist_non_success(
                    storage,
                    article=article,
                    ticker=ticker,
                    status=ClassificationStatus.FILTERED,
                    attempted_at=now,
                    prompt_version=prompt_version,
                    model_version=model_version,
                    safe_error=reason,
                )
                filtered += 1
                continue
            if classifier is None:
                _persist_non_success(
                    storage,
                    article=article,
                    ticker=ticker,
                    status=ClassificationStatus.DEFERRED,
                    attempted_at=now,
                    prompt_version=prompt_version,
                    model_version=model_version,
                    safe_error="classifier unavailable",
                )
                deferred += 1
                continue
            if (
                pass_calls >= CLASSIFIER_CALLS_PER_PASS
                or day_calls >= CLASSIFIER_CALLS_PER_UTC_DAY
            ):
                _persist_non_success(
                    storage,
                    article=article,
                    ticker=ticker,
                    status=ClassificationStatus.DEFERRED,
                    attempted_at=now,
                    prompt_version=prompt_version,
                    model_version=model_version,
                    safe_error="classifier budget exhausted",
                )
                deferred += 1
                continue
            try:
                result = classifier.classify(article, ticker)
            except Exception as error:
                result = NewsClassification(
                    article_id=article.article_id,
                    ticker=ticker,
                    prompt_version=prompt_version,
                    model_version=model_version,
                    status=ClassificationStatus.FAILED,
                    attempted_at=now,
                    safe_error=_safe_text(
                        str(error)
                        if isinstance(error, NewsClassifierError)
                        else "classifier request failed"
                    ),
                )
            result = replace(
                result,
                article_id=article.article_id,
                ticker=ticker,
                prompt_version=prompt_version,
                model_version=model_version,
            )
            storage.record_classifier_call(utc_day)
            pass_calls += 1
            day_calls += 1
            storage.save_news_classification(result)
            if not is_qualifying_classification(result):
                if result.status is ClassificationStatus.SUCCEEDED:
                    filtered += 1
                continue
            signal = news_signal_from_classification(article, result)
            handling = manager.handle_signal(signal)
            if handling.accepted:
                accepted.append(signal.signal_id)

    return NewsIngestResult(
        classifier_calls=pass_calls,
        accepted_signal_ids=tuple(accepted),
        diagnostics=tuple(diagnostics),
        filtered=filtered,
        deferred=deferred,
    )


def news_signal_from_classification(
    article: NewsArticle,
    classification: NewsClassification,
) -> NewsSignal:
    """Convert one qualifying classification into the existing news-signal contract."""

    if not is_qualifying_classification(classification):
        raise ValueError("classification is not eligible to become a signal")
    assert classification.category is not None
    assert classification.importance is not None
    assert classification.direction is not None
    return NewsSignal(
        signal_id=stable_signal_id(
            "news",
            article.provider,
            article.provider_article_id,
            classification.ticker,
            classification.prompt_version,
            classification.model_version,
        ),
        ticker=classification.ticker,
        occurred_at=article.created_at,
        importance=classification.importance,
        source_details=SourceDetails(
            provider=article.provider,
            source=article.source,
            feed=None,
            retrieved_at=article.retrieved_at,
        ),
        category=classification.category.value,
        direction=_signal_direction(classification.direction),
        headline=article.headline,
        matched_phrase=classification.category.value,
    )


def _watched_tickers(
    article: NewsArticle,
    watchlist: tuple[str, ...],
) -> tuple[str, ...]:
    mentioned = set(article.symbols)
    return tuple(ticker for ticker in watchlist if ticker in mentioned)


def _filter_reason(
    article: NewsArticle,
    ticker: str,
    *,
    storage: SQLiteStorage,
    window_start: datetime,
    prompt_version: str,
    model_version: str,
) -> str | None:
    if not article.headline or not article.source or not article.url:
        return "missing required fields"
    if article.created_at < window_start and article.updated_at < window_start:
        return "outside recovery window"
    if storage.canonical_url_is_processed(
        article.canonical_url,
        ticker,
        excluding_article_id=article.article_id,
        prompt_version=prompt_version,
        model_version=model_version,
    ):
        return "duplicate canonical url"
    return None


def _persist_non_success(
    storage: SQLiteStorage,
    *,
    article: NewsArticle,
    ticker: str,
    status: ClassificationStatus,
    attempted_at: datetime,
    prompt_version: str,
    model_version: str,
    safe_error: str,
) -> None:
    storage.save_news_classification(
        NewsClassification(
            article_id=article.article_id,
            ticker=ticker,
            prompt_version=prompt_version,
            model_version=model_version,
            status=status,
            attempted_at=attempted_at,
            safe_error=_safe_text(safe_error),
        )
    )


def _signal_direction(direction: NewsDirection) -> SignalDirection | None:
    if direction is NewsDirection.UP:
        return SignalDirection.UP
    if direction is NewsDirection.DOWN:
        return SignalDirection.DOWN
    return None


def _safe_text(value: str) -> str:
    text = " ".join(value.split())
    if not text:
        return "news processing failed"
    return text[:MAX_SAFE_ERROR_CHARS]
