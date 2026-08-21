"""Alpaca REST news boundary, normalization, and in-memory fake."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from investment_assistant.clock import Clock
from investment_assistant.market_data import parse_alpaca_timestamp
from investment_assistant.models import (
    MAX_NEWS_CONTENT_CHARS,
    MAX_NEWS_HEADLINE_CHARS,
    MAX_NEWS_SUMMARY_CHARS,
    NewsArticle,
)

logger = logging.getLogger(__name__)

ALPACA_NEWS_PROVIDER = "alpaca"
NEWS_API_PATH = "/v1beta1/news"
DATA_API_URL = "https://data.alpaca.markets"
NEWS_PAGE_SIZE = 50
NEWS_MAX_PAGES_PER_PASS = 10
NEWS_REQUEST_TIMEOUT_SECONDS = 30
MAX_NEWS_RETRIES = 5
MAX_BACKOFF_SECONDS = 32.0
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class NewsProviderError(Exception):
    """A news-provider failure that is safe to log."""


class NewsPermissionError(NewsProviderError):
    """The account is not permitted to query news."""


@dataclass(frozen=True, slots=True)
class NewsPage:
    """One page of already-normalized news articles."""

    articles: tuple[NewsArticle, ...]
    next_page_token: str | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NewsHttpResponse:
    """One HTTP-shaped news response used by the Alpaca adapter."""

    status_code: int
    body: Mapping[str, object]
    headers: Mapping[str, str] = field(default_factory=dict)


class NewsHttp(Protocol):
    """Fetch one Alpaca news page. Tests inject a fake."""

    def get_news(self, params: Mapping[str, str]) -> NewsHttpResponse:
        """GET /v1beta1/news with the given query parameters."""
        ...


class NewsProvider(Protocol):
    """Provider-agnostic paginated news retrieval."""

    def fetch_news(
        self,
        *,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
        page_token: str | None = None,
    ) -> NewsPage:
        """Return one oldest-first page of normalized articles."""
        ...


class FakeNewsProvider:
    """In-memory news provider for tests. Makes no network calls."""

    def __init__(
        self,
        pages: Sequence[NewsPage] = (),
        *,
        error: NewsProviderError | None = None,
    ) -> None:
        self._pages = list(pages)
        self._error = error
        self.requests: list[dict[str, object]] = []

    def fetch_news(
        self,
        *,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
        page_token: str | None = None,
    ) -> NewsPage:
        """Return the next scripted page, or raise the scripted error."""

        self.requests.append(
            {
                "symbols": tuple(symbol.strip().upper() for symbol in symbols),
                "start": start,
                "end": end,
                "page_token": page_token,
            }
        )
        if self._error is not None:
            raise self._error
        if not self._pages:
            return NewsPage(articles=())
        return self._pages.pop(0)


class AlpacaNewsProvider:
    """Normalize Alpaca REST news pages into internal articles."""

    def __init__(
        self,
        *,
        http: NewsHttp,
        clock: Clock,
        sleeper: Callable[[float], None],
        secret: str = "",
    ) -> None:
        self._http = http
        self._clock = clock
        self._sleeper = sleeper
        self._secret = secret

    def fetch_news(
        self,
        *,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
        page_token: str | None = None,
    ) -> NewsPage:
        """Fetch one REST page, retry 429s, and map valid articles immediately."""

        tickers = tuple(symbol.strip().upper() for symbol in symbols if symbol.strip())
        if not tickers:
            raise ValueError("news symbols must not be empty")
        params = {
            "symbols": ",".join(tickers),
            "start": _rfc3339(start),
            "end": _rfc3339(end),
            "sort": "asc",
            "limit": str(NEWS_PAGE_SIZE),
            "include_content": "true",
        }
        if page_token:
            params["page_token"] = page_token
        response = self._get_with_retry(params)
        if response.status_code in {401, 403}:
            logger.error(
                "Alpaca news permission denied",
                extra={"status_code": response.status_code},
            )
            raise NewsPermissionError(
                f"Alpaca news request failed with status {response.status_code}"
            )
        if response.status_code != 200:
            raise NewsProviderError(
                f"Alpaca news request failed with status {response.status_code}"
            )
        articles, diagnostics = articles_from_response(
            response.body,
            retrieved_at=self._clock.now(),
        )
        return NewsPage(
            articles=articles,
            next_page_token=_next_page_token(response.body),
            diagnostics=diagnostics,
        )

    def _get_with_retry(self, params: Mapping[str, str]) -> NewsHttpResponse:
        last_error = "Alpaca news request rate limited"
        for attempt in range(MAX_NEWS_RETRIES):
            response = self._http.get_news(params)
            if response.status_code != 429:
                return response
            if attempt == MAX_NEWS_RETRIES - 1:
                break
            delay = _retry_delay(response.headers, attempt)
            logger.warning(
                "Alpaca news rate limited; backing off",
                extra={"attempt": attempt + 1, "delay_seconds": delay},
            )
            self._sleeper(delay)
        raise NewsProviderError(last_error)


class UrllibNewsHttp:
    """GET Alpaca news with the standard library. Unused in pytest."""

    def __init__(
        self,
        *,
        key_id: str,
        secret: str,
        base_url: str = DATA_API_URL,
    ) -> None:
        self._key_id = key_id
        self._secret = secret
        self._base_url = base_url.rstrip("/")

    def get_news(self, params: Mapping[str, str]) -> NewsHttpResponse:
        """GET /v1beta1/news and return status, JSON body, and headers."""

        url = f"{self._base_url}{NEWS_API_PATH}?{urlencode(params)}"
        request = Request(
            url,
            headers={
                "APCA-API-KEY-ID": self._key_id,
                "APCA-API-SECRET-KEY": self._secret,
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=NEWS_REQUEST_TIMEOUT_SECONDS) as response:
                payload = response.read().decode("utf-8")
                parsed = json.loads(payload) if payload else {}
                if not isinstance(parsed, dict):
                    raise NewsProviderError(
                        "Alpaca news response must be a JSON object"
                    )
                return NewsHttpResponse(
                    status_code=int(response.status),
                    body=parsed,
                    headers={
                        key: str(value) for key, value in response.headers.items()
                    },
                )
        except HTTPError as error:
            raw = error.read().decode("utf-8")
            try:
                parsed_error = json.loads(raw) if raw else {}
                body = parsed_error if isinstance(parsed_error, dict) else {}
            except json.JSONDecodeError:
                body = {}
            return NewsHttpResponse(
                status_code=int(error.code),
                body=body,
                headers={key: str(value) for key, value in error.headers.items()},
            )
        except URLError as error:
            raise NewsProviderError("Alpaca news request failed") from error
        except json.JSONDecodeError as error:
            raise NewsProviderError(
                "Alpaca news response was not valid JSON"
            ) from error


def articles_from_response(
    body: Mapping[str, object],
    *,
    retrieved_at: datetime,
) -> tuple[tuple[NewsArticle, ...], tuple[str, ...]]:
    """Convert one Alpaca news payload, skipping malformed siblings."""

    raw_news = body.get("news", [])
    if raw_news is None:
        raw_news = []
    if not isinstance(raw_news, Sequence) or isinstance(raw_news, (str, bytes)):
        raise NewsProviderError("Alpaca news must be an array")
    articles: list[NewsArticle] = []
    diagnostics: list[str] = []
    for item in raw_news:
        if not isinstance(item, Mapping):
            diagnostics.append("skipped malformed Alpaca news item")
            logger.warning("Skipping malformed Alpaca news item")
            continue
        try:
            articles.append(article_from_alpaca(item, retrieved_at=retrieved_at))
        except (TypeError, ValueError) as error:
            diagnostics.append("skipped invalid Alpaca news article")
            logger.warning(
                "Skipping invalid Alpaca news article",
                extra={"reason": str(error)},
            )
    return tuple(articles), tuple(diagnostics)


def article_from_alpaca(
    payload: Mapping[str, object],
    *,
    retrieved_at: datetime,
) -> NewsArticle:
    """Convert one Alpaca news object into a validated internal article."""

    provider_article_id = _article_id(payload.get("id"))
    headline = _safe_text(_required_text(payload.get("headline"), "headline"))
    summary = _safe_text(_optional_text(payload.get("summary")))
    content = _safe_text(_optional_text(payload.get("content")))
    if not headline:
        raise ValueError("headline must not be blank")
    source = _required_text(payload.get("source"), "source")
    url = _required_text(payload.get("url"), "url")
    canonical = canonical_url(url)
    symbols = _symbols(payload.get("symbols"))
    if not symbols:
        raise ValueError("symbols must not be empty")
    bounded_headline = headline[:MAX_NEWS_HEADLINE_CHARS]
    bounded_summary = summary[:MAX_NEWS_SUMMARY_CHARS]
    bounded_content = content[:MAX_NEWS_CONTENT_CHARS]
    return NewsArticle(
        provider=ALPACA_NEWS_PROVIDER,
        provider_article_id=provider_article_id,
        symbols=symbols,
        headline=bounded_headline,
        summary=bounded_summary,
        content=bounded_content,
        url=url.strip(),
        canonical_url=canonical,
        source=source,
        created_at=parse_alpaca_timestamp(payload.get("created_at")),
        updated_at=parse_alpaca_timestamp(payload.get("updated_at")),
        retrieved_at=retrieved_at,
        content_fingerprint=content_fingerprint(
            canonical_url=canonical,
            headline=bounded_headline,
            summary=bounded_summary,
            content=bounded_content,
        ),
    )


def canonical_url(url: str) -> str:
    """Return a lowercase host/path fingerprint with query and fragment removed."""

    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        raise ValueError("url must include a scheme and host")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def content_fingerprint(
    *,
    canonical_url: str,
    headline: str,
    summary: str,
    content: str,
) -> str:
    """Return a stable hash of the bounded article text and canonical URL."""

    payload = "\n".join((canonical_url, headline, summary, content))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def strip_html(value: str) -> str:
    """Return visible text with tags, scripts, and styles removed."""

    extractor = _VisibleTextExtractor()
    extractor.feed(value)
    extractor.close()
    return extractor.text()


def _safe_text(value: str) -> str:
    without_markup = strip_html(value)
    without_control = _CONTROL_CHARS.sub(" ", without_markup)
    return " ".join(without_control.split())


def _article_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("id must be an integer or string")
    if isinstance(value, int) and value < 0:
        raise ValueError("id must not be negative")
    text = str(value).strip()
    if not text:
        raise ValueError("id must not be blank")
    return text


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _optional_text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("text field must be a string")
    return value


def _symbols(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("symbols must be an array")
    tickers: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        ticker = item.strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        tickers.append(ticker)
    return tuple(tickers)


def _next_page_token(body: Mapping[str, object]) -> str | None:
    token = body.get("next_page_token")
    if token is None:
        return None
    if not isinstance(token, str) or not token:
        return None
    return token


def _retry_delay(headers: Mapping[str, str], attempt: int) -> float:
    retry_after = _header(headers, "Retry-After")
    if retry_after is not None and retry_after.isdigit():
        return min(float(retry_after), MAX_BACKOFF_SECONDS)
    return min(2.0**attempt, MAX_BACKOFF_SECONDS)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _rfc3339(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("news start and end must be timezone-aware")
    return value.isoformat().replace("+00:00", "Z")


class _VisibleTextExtractor(HTMLParser):
    """Collect visible text while skipping script and style blocks."""

    _SKIP = frozenset({"script", "style"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._chunks.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self._chunks).split())
