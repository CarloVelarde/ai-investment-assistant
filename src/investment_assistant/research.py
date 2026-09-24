"""Bounded local research evidence and application-owned citation validation."""

import json
import re
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

from investment_assistant.clock import Clock
from investment_assistant.models import (
    Event,
    EvidencePacket,
    EvidenceSnapshot,
    EvidenceSource,
    LiveReportDetails,
    MarketTimeframe,
    NewsSignal,
    PacketNews,
    PriorReportContext,
    ReportDraft,
    ResearchReport,
    ResearchToolResult,
    ResearchUsage,
    Signal,
    _as_utc,
)
from investment_assistant.research_http import Deadline, ResearchError
from investment_assistant.research_model import (
    RESEARCH_MODEL,
    RESEARCH_PROMPT_VERSION,
    FunctionCall,
    ModelTurn,
    ResearchModel,
    research_request,
)
from investment_assistant.sec import SecClient, ToolResult

if TYPE_CHECKING:
    from investment_assistant.storage import SQLiteStorage


class SourceRegistry:
    """Only evidence observed in this run may supply report source metadata."""

    def __init__(self, sources: tuple[EvidenceSource, ...]) -> None:
        self._sources: dict[str, EvidenceSource] = {}
        for source in sources:
            self.add(source)

    def add(self, source: EvidenceSource) -> None:
        old = self._sources.get(source.reference)
        if old is not None and old != source:
            raise ValueError("conflicting evidence reference")
        self._sources[source.reference] = source

    def resolve(self, draft: ReportDraft) -> tuple[EvidenceSource, ...]:
        references = sorted(
            {ref for item in draft.evidence_items() for ref in item.references}
        )
        if len(references) > 20:
            raise ValueError("report exceeds 20 sources")
        if any(ref not in self._sources for ref in references):
            raise ValueError("unknown evidence reference")
        return tuple(self._sources[ref] for ref in references)


def build_evidence_packet(
    storage: SQLiteStorage,
    event_id: str,
    event_update: int,
    *,
    as_of: datetime,
) -> EvidencePacket:
    """Read one consistent local view, without config or external retrieval."""
    as_of = _as_utc(as_of, "as_of")
    with storage.transaction():
        event = storage.get_event(event_id)
        if (
            event is None
            or event.current_update != event_update
            or event.updated_at > as_of
        ):
            raise ValueError("event is outside the captured update")
        signals, total = storage.read_research_signals(event, as_of=as_of)
        if not signals:
            raise ValueError("research requires an available trigger")
        sources = [
            EvidenceSource(
                reference=f"signal:{s.signal_id}",
                title=f"{s.ticker} saved signal",
                identity=s.signal_id,
                kind="packet",
                published_at=s.occurred_at,
                retrieved_at=s.source_details.retrieved_at,
            )
            for s in signals
        ]
        latest_signal = max(s.occurred_at for s in signals)
        symbols = tuple(dict.fromkeys((event.ticker, "SPY")))
        daily = tuple(
            bar
            for symbol in symbols
            for bar in storage.read_research_bars(
                symbol, MarketTimeframe.ONE_DAY, as_of=as_of, end_at=as_of
            )
        )
        minutes = tuple(
            bar
            for symbol in symbols
            for bar in storage.read_research_bars(
                symbol, MarketTimeframe.ONE_MINUTE, as_of=as_of, end_at=latest_signal
            )
        )
        for bar in (*daily, *minutes):
            sources.append(
                EvidenceSource(
                    reference=bar.bar_id,
                    identity=bar.bar_id,
                    title=f"{bar.ticker} {bar.timeframe} bar",
                    kind="packet",
                    published_at=bar.end_at,
                    retrieved_at=bar.retrieved_at,
                )
            )
        gaps = [
            "Company and sector are unavailable.",
            "Stored bars and articles may be revised; original signal measurements are retained.",
        ]
        for symbol in symbols:
            if not any(b.ticker == symbol for b in daily):
                gaps.append(f"Completed daily history unavailable for {symbol}.")
            if not any(b.ticker == symbol for b in minutes):
                gaps.append(f"Regular-session minute history unavailable for {symbol}.")
        links: dict[str, list[NewsSignal]] = {}
        for signal in signals:
            if isinstance(signal, NewsSignal):
                if signal.article_id is None:
                    gaps.append(
                        f"Article linkage unavailable for signal {signal.signal_id}."
                    )
                else:
                    links.setdefault(signal.article_id, []).append(signal)
        # IDs originate only from the bounded signal selection. Each lookup is a
        # primary-key read; no article or classification table is loaded wholesale.
        articles = []
        for article_id in links:
            article = storage.get_news_article(article_id)
            if (
                article is None
                or article.retrieved_at > as_of
                or article.updated_at > as_of
                or article.created_at > as_of
            ):
                gaps.append(
                    f"Linked article unavailable as of packet time: {article_id}."
                )
            else:
                articles.append(article)
        articles.sort(key=lambda a: (a.created_at, a.article_id), reverse=True)
        news = []
        for article in articles[:5]:
            classifications = []
            keys = set()
            for signal in links[article.article_id]:
                key = (
                    signal.classification_prompt_version,
                    signal.classification_model_version,
                )
                if key in keys:
                    continue
                keys.add(key)
                prompt, model = key
                classification = (
                    None
                    if prompt is None or model is None
                    else storage.get_news_classification(
                        article.article_id,
                        event.ticker,
                        prompt_version=prompt,
                        model_version=model,
                    )
                )
                if classification is None or classification.attempted_at > as_of:
                    gaps.append(
                        f"Original classification unavailable for signal {signal.signal_id}."
                    )
                else:
                    classifications.append(classification)
            # The entire article text allowance includes headline and summary.
            remaining = max(0, 4000 - len(article.headline) - len(article.summary))
            bounded = replace(article, content=article.content[:remaining])
            news.append(
                PacketNews(
                    article=bounded,
                    classifications=tuple(classifications),
                    revised_since_signal=any(
                        article.updated_at > s.source_details.retrieved_at
                        or article.headline != s.headline
                        for s in links[article.article_id]
                    ),
                    text_truncated=bounded.content != article.content,
                )
            )
            sources.append(
                EvidenceSource(
                    reference=f"news:{article.article_id}",
                    identity=article.canonical_url,
                    title=article.headline,
                    kind="news",
                    published_at=article.created_at,
                    retrieved_at=article.retrieved_at,
                )
            )
        prior = storage.previous_research_report(event, as_of=as_of)
        context = (
            None
            if prior is None
            else PriorReportContext(
                report_id=prior.report_id,
                summary=prior.summary[:2000],
                is_fake=prior.is_fake,
                created_at=prior.created_at,
            )
        )
        packet = EvidencePacket.model_construct(
            event=event,
            as_of=as_of,
            signals=signals,
            signal_total=total,
            signals_omitted=total - len(signals),
            daily_bars=daily,
            minute_bars=minutes,
            news=tuple(news),
            articles_omitted=max(0, len(articles) - 5),
            prior_report=context,
            sources=tuple(sources),
            gaps=tuple(gaps),
        )
    # Construct temporarily to measure serialized JSON without accepting an
    # oversized packet. Revalidate once optional evidence has been trimmed.
    while len(packet.model_dump_json()) > 40_000:
        trimmed = False
        news_items = list(packet.news)
        for index in range(len(news_items) - 1, -1, -1):
            item = news_items[index]
            if item.article.content or item.article.summary:
                news_items[index] = item.model_copy(
                    update={
                        "article": replace(item.article, content="", summary=""),
                        "text_truncated": True,
                    }
                )
                packet = packet.model_copy(update={"news": tuple(news_items)})
                trimmed = True
                break
        if trimmed:
            continue
        bars = (*packet.daily_bars, *packet.minute_bars)
        if not bars:
            raise ValueError("required evidence packet exceeds 40000 characters")
        oldest = min(bars, key=lambda b: (b.end_at, b.bar_id))
        packet = packet.model_copy(
            update={
                "daily_bars": tuple(
                    b for b in packet.daily_bars if b.bar_id != oldest.bar_id
                ),
                "minute_bars": tuple(
                    b for b in packet.minute_bars if b.bar_id != oldest.bar_id
                ),
                "bar_rows_omitted": packet.bar_rows_omitted + 1,
                "sources": tuple(
                    s for s in packet.sources if s.reference != oldest.bar_id
                ),
            }
        )
    return EvidencePacket.model_validate(packet.model_dump())


def create_live_report(
    packet: EvidencePacket,
    draft: ReportDraft,
    *,
    attempt_id: str,
    created_at: datetime,
    model_version: str,
    prompt_version: str,
    usage: ResearchUsage,
    evidence: tuple[EvidenceSnapshot, ...] = (),
) -> ResearchReport:
    """Bind model analysis to application-owned event identity and source data."""
    registry = SourceRegistry(packet.sources)
    for item in evidence:
        registry.add(item.source)
    details = LiveReportDetails(
        company=packet.company,
        triggering_signal_ids=tuple(s.signal_id for s in packet.signals),
        analysis=draft,
        sources=registry.resolve(draft),
        model_version=model_version,
        prompt_version=prompt_version,
        attempt_id=attempt_id,
        packet_as_of=packet.as_of,
        usage=usage,
    )
    return ResearchReport(
        report_id=f"report:{packet.event.event_id}:{packet.event.current_update}",
        event_id=packet.event.event_id,
        event_update=packet.event.current_update,
        ticker=packet.event.ticker,
        event_occurred_at=packet.event.created_at,
        created_at=created_at,
        summary=draft.summary,
        is_fake=False,
        details=details,
    )


class ResearchDeferred(Exception):
    """No model start was reserved; live scheduling will retry when eligible."""


class ResearchRunner:
    """One bounded investigation through the existing event/researcher boundary.

    Returns a validated report; the event manager owns atomic report persistence
    and notification. A missing model means no configured API key.
    """

    def __init__(
        self,
        *,
        storage: SQLiteStorage,
        model: ResearchModel | None,
        sec: SecClient,
        clock: Clock,
        monotonic: Callable[[], float] = time.monotonic,
        daily_starts: int = 20,
        budget_microdollars: int | None = None,
    ) -> None:
        self._storage = storage
        self._model = model
        self._sec = sec
        self._clock = clock
        self._monotonic = monotonic
        self._daily_starts = daily_starts
        self._budget_microdollars = budget_microdollars

    def __call__(self, event: Event, signals: tuple[Signal, ...]) -> ResearchReport:
        deadline = Deadline.start(self._monotonic)
        attempt = self._storage.reserve_research_attempt(
            event.event_id,
            event.current_update,
            now=self._clock.now(),
            model_version=RESEARCH_MODEL,
            prompt_version=RESEARCH_PROMPT_VERSION,
            has_api_key=self._model is not None and self._model.configured,
            daily_starts=self._daily_starts,
            budget_microdollars=self._budget_microdollars,
        )
        if attempt is None:
            raise ResearchDeferred("Research deferred before any provider call.")
        try:
            deadline.remaining()
            packet = build_evidence_packet(
                self._storage,
                event.event_id,
                event.current_update,
                as_of=self._clock.now(),
            )
            deadline.remaining()
            return self._investigate(attempt.attempt_id, packet, deadline)
        except Exception:
            self._storage.fail_research_attempt(
                attempt.attempt_id, finished_at=self._clock.now()
            )
            raise ResearchError(
                "Research failed; the current update may be retried."
            ) from None
        finally:
            if self._budget_microdollars is not None:
                self._storage.finish_model_run(attempt.attempt_id)

    def _investigate(
        self, attempt_id: str, packet: EvidencePacket, deadline: Deadline
    ) -> ResearchReport:
        assert self._model is not None
        session = self._sec.start_run(packet.event.ticker, packet.as_of)
        inputs: list[dict[str, object]] = [
            {"role": "user", "content": packet.model_dump_json()}
        ]
        evidence: dict[str, EvidenceSnapshot] = {}
        cached: dict[tuple[str, str], ToolResult] = {}
        usage = ResearchUsage()
        tool_results: list[ResearchToolResult] = []
        forced_final = False
        seen_call_ids: set[str] = set()

        def persist() -> None:
            nonlocal usage
            usage = usage.model_copy(update={"sec_http_calls": session.http_calls})
            self._storage.save_research_evidence(
                attempt_id,
                packet=packet,
                evidence=tuple(evidence.values()),
                usage=usage,
                tool_results=tuple(tool_results),
            )
            deadline.remaining()

        persist()
        try:
            for turn_number in range(5):
                final = forced_final or turn_number == 4
                search_cap = (
                    0
                    if final
                    else min(3 - usage.web_search_calls, 6 - usage.tool_slots)
                )
                _fit_continuation(inputs)
                # Enforce request size for injected models as well as the real adapter.
                research_request(
                    tuple(inputs), search_cap=search_cap, tools_enabled=not final
                )
                deadline.remaining()
                usage = usage.model_copy(update={"model_calls": usage.model_calls + 1})
                persist()
                request_number = (
                    self._storage.start_research_request(
                        attempt_id, search_slots=search_cap
                    )
                    if self._budget_microdollars is not None
                    else None
                )
                response: ModelTurn | None = None
                try:
                    response = self._model.respond(
                        tuple(inputs),
                        search_cap=search_cap,
                        tools_enabled=not final,
                        deadline=deadline,
                    )
                finally:
                    if request_number is not None:
                        observed = (
                            (response.input_tokens, response.output_tokens)
                            if response is not None
                            and response.input_tokens is not None
                            and response.output_tokens is not None
                            else getattr(self._model, "last_usage", None)
                        )
                        searches = (
                            response.web_calls
                            if response is not None
                            else getattr(self._model, "last_search_calls", None)
                        )
                        self._storage.settle_model_request(
                            attempt_id,
                            request_number,
                            usage=observed,
                            search_calls=searches,
                        )
                assert response is not None
                deadline.remaining()
                if (
                    response.web_calls < 0
                    or response.web_calls > search_cap
                    or (final and response.functions)
                    or (
                        response.output_tokens is not None
                        and response.output_tokens > 4000
                    )
                ):
                    raise ResearchError(
                        "Research provider exceeded a request allowance."
                    )
                usage = ResearchUsage.model_validate(
                    {
                        **usage.model_dump(),
                        "tool_slots": usage.tool_slots + response.web_calls,
                        "web_search_calls": usage.web_search_calls + response.web_calls,
                        "input_tokens": _add_tokens(
                            usage.input_tokens, response.input_tokens
                        ),
                        "output_tokens": _add_tokens(
                            usage.output_tokens, response.output_tokens
                        ),
                    }
                )
                for snapshot in response.evidence:
                    # Stable first-seen metadata avoids conflicting retrieval times when
                    # hosted search returns the same URL on a later model turn.
                    evidence.setdefault(snapshot.source.reference, snapshot)
                if len(evidence) > 20:
                    raise ResearchError("Research evidence exceeds the snapshot limit.")
                persist()
                if response.draft is not None and not response.functions:
                    report = create_live_report(
                        packet,
                        response.draft,
                        attempt_id=attempt_id,
                        created_at=self._clock.now(),
                        model_version=RESEARCH_MODEL,
                        prompt_version=RESEARCH_PROMPT_VERSION,
                        usage=usage,
                        evidence=tuple(evidence.values()),
                    )
                    current = self._storage.get_event(packet.event.event_id)
                    if (
                        current is None
                        or current.current_update != packet.event.current_update
                    ):
                        raise ResearchError("Research result is stale.")
                    deadline.remaining()
                    return report
                if final:
                    raise ResearchError("Final research report missing.")
                inputs.extend(response.continuation)
                forced_final = _cap_reached(usage) or turn_number == 3
                for call in response.functions:
                    if call.call_id in seen_call_ids:
                        raise ResearchError("Research function call ID was reused.")
                    seen_call_ids.add(call.call_id)
                    if forced_final:
                        result = ToolResult(
                            '{"status":"limit","reason":"Research tool allowance exhausted."}'
                        )
                    else:
                        usage = usage.model_copy(
                            update={"tool_slots": usage.tool_slots + 1}
                        )
                        valid = _normalized_function(call)
                        if valid is None:
                            result = ToolResult(
                                '{"status":"unavailable","reason":"Invalid research function request."}'
                            )
                        elif valid in cached:
                            result = cached[valid]
                        else:
                            deadline.remaining()
                            if call.name == "get_recent_filings":
                                usage = usage.model_copy(
                                    update={
                                        "filing_list_calls": usage.filing_list_calls + 1
                                    }
                                )
                                persist()
                                result = session.get_recent_filings(deadline)
                            else:
                                usage = usage.model_copy(
                                    update={
                                        "filing_excerpt_calls": usage.filing_excerpt_calls
                                        + 1
                                    }
                                )
                                persist()
                                result = session.get_filing_excerpt(valid[1], deadline)
                            deadline.remaining()
                            if len(result.text) > 8000:
                                raise ResearchError(
                                    "Research tool result exceeds its size limit."
                                )
                            cached[valid] = result
                        tool_results.append(
                            ResearchToolResult(
                                name=(
                                    "get_recent_filings"
                                    if call.name == "get_recent_filings"
                                    else "get_filing_excerpt"
                                    if call.name == "get_filing_excerpt"
                                    else "invalid"
                                ),
                                filing_id=valid[1]
                                if valid is not None
                                and call.name == "get_filing_excerpt"
                                else None,
                                output=result.text,
                            )
                        )
                        forced_final = _cap_reached(usage)
                    for snapshot in result.evidence:
                        evidence.setdefault(snapshot.source.reference, snapshot)
                    inputs.append(
                        {
                            "type": "function_call_output",
                            "call_id": call.call_id,
                            "output": result.text,
                        }
                    )
                    persist()
                if not response.functions and not response.continuation:
                    raise ResearchError(
                        "Research response contains no report or continuation."
                    )
            raise ResearchError("Research turn allowance exhausted.")
        finally:
            # Preserve the calls already made even if a provider or deadline
            # interrupted the run before the next normal evidence checkpoint.
            usage = usage.model_copy(update={"sec_http_calls": session.http_calls})
            self._storage.save_research_evidence(
                attempt_id,
                packet=packet,
                evidence=tuple(evidence.values()),
                usage=usage,
                tool_results=tuple(tool_results),
            )
            deadline.remaining()


def _cap_reached(usage: ResearchUsage) -> bool:
    return (
        usage.tool_slots >= 6
        or usage.web_search_calls >= 3
        or usage.filing_list_calls >= 2
        or usage.filing_excerpt_calls >= 1
    )


def _normalized_function(call: FunctionCall) -> tuple[str, str] | None:
    try:
        arguments: object = json.loads(call.arguments)
    except ValueError, TypeError:
        return None
    if not isinstance(arguments, dict):
        return None
    if call.name == "get_recent_filings" and arguments == {}:
        return (call.name, "{}")
    if call.name == "get_filing_excerpt" and set(arguments) == {"filing_id"}:
        filing_id = arguments["filing_id"]
        if isinstance(filing_id, str) and re.fullmatch(
            r"[0-9]{10}-[0-9]{2}-[0-9]{6}", filing_id.strip()
        ):
            return (call.name, filing_id.strip())
    return None


def _add_tokens(total: int | None, returned: int | None) -> int | None:
    if returned is None:
        return total
    if isinstance(returned, bool) or returned < 0:
        raise ResearchError("Research token usage is invalid.")
    return (total or 0) + returned


def _fit_continuation(inputs: list[dict[str, object]]) -> None:
    """Trim optional old assistant prose; preserve all tool calls and answers.

    Encrypted reasoning and search items are protocol state and cannot be cut.
    The final payload guard fails safely if required continuation will not fit.
    """
    while len(json.dumps(inputs, ensure_ascii=False, separators=(",", ":"))) > 90_000:
        index = next(
            (i for i, item in enumerate(inputs) if item.get("type") == "message"), None
        )
        if index is None:
            break
        inputs.pop(index)
