"""Narrow SEC submissions and filing excerpts, bound to one event ticker."""

import json
import math
import re
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from investment_assistant.clock import Clock
from investment_assistant.models import EvidenceSnapshot, EvidenceSource
from investment_assistant.research_http import (
    MAX_RESPONSE_BYTES,
    Deadline,
    HttpResult,
    ResearchError,
    ResearchHttp,
    ResearchTimeout,
)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


@dataclass(frozen=True)
class ToolResult:
    text: str
    evidence: tuple[EvidenceSnapshot, ...] = ()


def unavailable() -> ToolResult:
    return ToolResult('{"status":"unavailable","reason":"SEC evidence unavailable."}')


@dataclass(frozen=True)
class Filing:
    filing_id: str
    form: str
    published_at: datetime
    url: str


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        if not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.hidden:
            self.hidden -= 1
        if not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


class SecClient:
    """Reuse ticker metadata, pacing, and rate-limit state across research runs."""

    def __init__(
        self,
        *,
        http: ResearchHttp,
        clock: Clock,
        user_agent: str = "",
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http
        self._clock = clock
        self._user_agent = user_agent.strip()
        self._monotonic = monotonic
        self._sleep = sleep
        self._ciks: dict[str, str] | None = None
        self._next_request = 0.0
        self._disabled_until = 0.0
        self._lock = threading.Lock()
        self.request_count = 0

    def start_run(self, ticker: str, as_of: datetime) -> SecSession:
        return SecSession(self, ticker, as_of)

    def _get(self, url: str, deadline: Deadline) -> HttpResult:
        if not self._user_agent or "\n" in self._user_agent or "\r" in self._user_agent:
            raise ResearchError("SEC user-agent unavailable.")
        deadline.remaining()
        if not self._lock.acquire(timeout=deadline.remaining()):
            raise ResearchTimeout("SEC pacing deadline expired.")
        try:
            if self._monotonic() < self._disabled_until:
                raise ResearchError("SEC requests temporarily deferred.")
            deadline.wait(max(0, self._next_request - self._monotonic()), self._sleep)
            self._next_request = self._monotonic() + 0.5
            self.request_count += 1
            response = self._http.request(
                "GET",
                url,
                headers={
                    "User-Agent": self._user_agent,
                    "Accept-Encoding": "identity",
                    "Accept": "application/json,text/html,text/plain",
                },
                body=None,
                deadline=deadline,
                timeout=min(10, deadline.remaining()),
            )
            deadline.remaining()
            headers = {k.lower(): v for k, v in response.headers.items()}
            if response.status == 429 or "retry-after" in headers:
                delay = 60.0
                value = headers.get("retry-after", "")
                try:
                    parsed_delay = float(value)
                    if not math.isfinite(parsed_delay) or parsed_delay < 0:
                        raise ValueError("invalid retry delay")
                    delay = parsed_delay
                except ValueError:
                    with suppress(ValueError, TypeError, OverflowError):
                        delay = max(
                            0.0,
                            (
                                parsedate_to_datetime(value) - self._clock.now()
                            ).total_seconds(),
                        )
                self._disabled_until = self._monotonic() + delay
                raise ResearchError("SEC requests temporarily deferred.")
            if response.status != 200 or len(response.body) > MAX_RESPONSE_BYTES:
                raise ResearchError("SEC request unavailable.")
            return response
        finally:
            self._lock.release()

    def _cik(self, ticker: str, deadline: Deadline) -> str | None:
        if self._ciks is None:
            response = self._get(TICKERS_URL, deadline)
            data = _json_object(response)
            ciks = {}
            for entry in data.values():
                if not isinstance(entry, dict):
                    continue
                symbol, cik = entry.get("ticker"), entry.get("cik_str")
                if (
                    isinstance(symbol, str)
                    and re.fullmatch(r"[0-9]{1,10}", str(cik))
                    and int(str(cik)) > 0
                ):
                    ciks[symbol.upper()] = str(cik).zfill(10)
            self._ciks = ciks
        return self._ciks.get(ticker.upper())


class SecSession:
    """The model may select only a filing actually returned in this run."""

    def __init__(self, client: SecClient, ticker: str, as_of: datetime) -> None:
        self._client = client
        self._ticker = ticker
        self._as_of = as_of
        self._filings: dict[str, Filing] = {}
        self._initial_requests = client.request_count

    @property
    def http_calls(self) -> int:
        return self._client.request_count - self._initial_requests

    def get_recent_filings(self, deadline: Deadline) -> ToolResult:
        try:
            cik = self._client._cik(self._ticker, deadline)
            if cik is None:
                return unavailable()
            data = _json_object(
                self._client._get(
                    f"https://data.sec.gov/submissions/CIK{cik}.json", deadline
                )
            )
            filings = data.get("filings")
            if not isinstance(filings, dict) or not isinstance(
                filings.get("recent"), dict
            ):
                return unavailable()
            recent = filings["recent"]
            columns = [
                recent.get(key)
                for key in ("accessionNumber", "form", "filingDate", "primaryDocument")
            ]
            if not all(isinstance(column, list) for column in columns):
                return unavailable()
            accession, forms, dates, documents = columns
            assert isinstance(accession, list) and isinstance(forms, list)
            assert isinstance(dates, list) and isinstance(documents, list)
            if (
                len({len(column) for column in (accession, forms, dates, documents)})
                != 1
            ):
                return unavailable()
            selected = []
            for acc, form, date, document in zip(
                accession, forms, dates, documents, strict=True
            ):
                if (
                    form not in ("8-K", "10-Q", "10-K")
                    or not isinstance(acc, str)
                    or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", acc)
                    or not isinstance(document, str)
                    or not re.fullmatch(
                        r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:htm|html|txt)", document
                    )
                    or ".." in document
                    or not isinstance(date, str)
                ):
                    continue
                try:
                    published = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
                except ValueError:
                    continue
                if (
                    not self._as_of.date() - timedelta(days=90)
                    <= published.date()
                    <= self._as_of.date()
                ):
                    continue
                url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{document}"
                selected.append(Filing(acc, form, published, url))
            selected.sort(key=lambda f: (f.published_at, f.filing_id), reverse=True)
            selected = selected[:10]
            evidence = tuple(
                EvidenceSnapshot(
                    source=self._source(filing, excerpt=False),
                    text=f"SEC {filing.form} filing listed; contents have not yet been retrieved.",
                )
                for filing in selected
            )
            text = json.dumps(
                {
                    "status": "available",
                    "filings": [
                        {
                            "filing_id": f.filing_id,
                            "form": f.form,
                            "published_at": f.published_at.isoformat(),
                            "reference": e.source.reference,
                            "url": f.url,
                        }
                        for f, e in zip(selected, evidence, strict=True)
                    ],
                },
                separators=(",", ":"),
            )
            if len(text) > 8000:
                return unavailable()
            self._filings.update((f.filing_id, f) for f in selected)
            deadline.remaining()
            return ToolResult(text, evidence)
        except ResearchTimeout:
            deadline.remaining()
            return unavailable()
        except Exception:
            return unavailable()

    def _source(self, filing: Filing, *, excerpt: bool) -> EvidenceSource:
        return EvidenceSource(
            reference=f"filing:{filing.filing_id}:{'excerpt' if excerpt else 'listing'}",
            identity=filing.url,
            title=f"SEC {filing.form} {filing.filing_id}",
            kind="filing",
            published_at=filing.published_at,
            retrieved_at=self._client._clock.now(),
        )

    def get_filing_excerpt(self, filing_id: str, deadline: Deadline) -> ToolResult:
        filing = self._filings.get(filing_id)
        if filing is None:
            return unavailable()
        try:
            response = self._client._get(filing.url, deadline)
            content_type = response.headers.get("content-type", "").lower()
            if not any(
                kind in content_type
                for kind in ("text/html", "text/plain", "application/xhtml+xml")
            ):
                return unavailable()
            parser = _PlainText()
            parser.feed(response.body.decode("utf-8", errors="replace"))
            plain = " ".join("".join(parser.parts).split())
            if not plain:
                return unavailable()
            source = self._source(filing, excerpt=True)
            text = plain[:8000]
            truncated = response.truncated or len(plain) > len(text)
            # The serialized tool result, including provenance, also fits 8000.
            while True:
                result = json.dumps(
                    {
                        "status": "available",
                        "source": source.model_dump(mode="json"),
                        "text": text,
                        "truncated": truncated,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if len(result) <= 8000:
                    break
                text = text[: max(0, len(text) - (len(result) - 8000))]
                truncated = True
            deadline.remaining()
            return ToolResult(
                result,
                (EvidenceSnapshot(source=source, text=text, truncated=truncated),),
            )
        except ResearchTimeout:
            deadline.remaining()
            return unavailable()
        except Exception:
            return unavailable()


def _json_object(response: HttpResult) -> dict[str, object]:
    if response.truncated:
        raise ResearchError("SEC metadata exceeds size limit.")
    value: object = json.loads(response.body)
    if not isinstance(value, dict):
        raise ResearchError("SEC metadata is invalid.")
    return value
