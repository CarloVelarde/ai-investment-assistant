"""Bounded research replay through the real adapters and event manager."""

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from investment_assistant.clock import FixedClock
from investment_assistant.event_manager import EventManager
from investment_assistant.model_budget import RESEARCH_MODEL, token_charge
from investment_assistant.models import (
    Event,
    EventStatus,
    EvidencePacket,
    LiveReportDetails,
    ResearchReport,
)
from investment_assistant.news_ingest import news_signal_from_classification
from investment_assistant.research import ResearchDeferred, ResearchRunner
from investment_assistant.research_http import Deadline, HttpResult, ResearchError
from investment_assistant.research_model import OpenAIResearchModel
from investment_assistant.storage import SQLiteStorage
from test_research_foundation import (
    EVENT,
    NOW,
    SIGNAL,
    article,
    classification,
    draft,
    seed,
)
from test_research_model import completed, function, message, web
from test_sec import ScriptedHttp, Timer, client, metadata, response, submissions


def runner(
    storage: SQLiteStorage,
    timer: Timer,
    model_http: ScriptedHttp,
    sec_http: ScriptedHttp | None = None,
    *,
    key: bool = True,
    budget_microdollars: int | None = None,
) -> ResearchRunner:
    sec_http = sec_http or ScriptedHttp([])
    model = (
        OpenAIResearchModel(
            http=model_http, clock=FixedClock(NOW), api_key="not-a-real-key"
        )
        if key
        else None
    )
    sec = client(
        sec_http, timer, "Test test@example.test" if sec_http.responses else ""
    )
    return ResearchRunner(
        storage=storage,
        model=model,
        sec=sec,
        clock=FixedClock(NOW),
        monotonic=timer,
        budget_microdollars=budget_microdollars,
    )


def saved_attempt(path: Path) -> Any:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT details FROM research_attempts ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    return json.loads(row[0])


def _details(report: ResearchReport) -> LiveReportDetails:
    assert report.details is not None
    return report.details


def payloads(http: ScriptedHttp) -> list[Any]:
    loaded: list[Any] = []
    for call in http.calls:
        body = call[3]
        assert body is not None
        loaded.append(json.loads(body))
    return loaded


def test_packet_only_report_through_event_manager_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "runner.db"
    timer = Timer()
    http = ScriptedHttp(
        [
            response(
                completed(
                    message(draft("signal:trigger").model_dump_json()),
                    usage={"input_tokens": 100, "output_tokens": 200},
                )
            )
        ]
    )
    with SQLiteStorage(path) as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(NOW))
        event = manager.handle_signal(SIGNAL).event
        assert event is not None
        delivered: list[ResearchReport] = []

        def notify(current: Event, report: ResearchReport) -> None:
            assert storage.get_report(report.report_id) == report
            assert report.details is not None
            attempt = storage.get_research_attempt(report.details.attempt_id)
            assert attempt is not None
            assert attempt.status == "SUCCEEDED"
            delivered.append(report)

        manager.process_pending(
            researcher=runner(storage, timer, http), notifier=notify
        )
        assert len(delivered) == 1
        assert delivered[0].details is not None
        assert delivered[0].details.analysis.cause_unknown
        assert delivered[0].details.usage.model_calls == 1
        assert delivered[0].details.usage.output_tokens == 200
        assert not delivered[0].is_fake
    with SQLiteStorage(path) as storage:
        storage.initialize()
        saved_event = storage.get_event(event.event_id)
        assert saved_event is not None
        assert saved_event.status == EventStatus.NOTIFIED
        EventManager(storage, clock=FixedClock(NOW)).process_pending(
            researcher=runner(storage, timer, http),
            notifier=lambda *args: pytest.fail("duplicate"),
        )
    assert len(http.calls) == 1


def test_invalid_model_output_keeps_independent_usage_charge(tmp_path: Path) -> None:
    timer = Timer()
    http = ScriptedHttp(
        [
            response(
                completed(
                    message("not valid report JSON"),
                    usage={
                        "input_tokens": 123,
                        "output_tokens": 45,
                    },
                )
            )
        ]
    )
    with SQLiteStorage(tmp_path / "invalid-budget.db") as storage:
        seed(storage)
        live = runner(storage, timer, http, budget_microdollars=2_000_000)
        with pytest.raises(ResearchError):
            live(EVENT, (SIGNAL,))
        assert len(http.calls) == 1
        assert storage.research_starts_on(NOW) == 1
        assert storage.model_budget_used(NOW.date().isoformat()) == token_charge(
            RESEARCH_MODEL, 123, 45
        )


def test_filings_excerpt_finalization_and_normalized_snapshots(tmp_path: Path) -> None:
    path = tmp_path / "filings.db"
    timer = Timer()
    final = draft().model_dump(mode="json")
    final["market_context"] = [
        {
            "text": "A filing discusses earnings.",
            "references": ["filing:0000012345-26-000000:excerpt"],
            "is_hypothesis": False,
        }
    ]
    reasoning = {
        "type": "reasoning",
        "id": "rs_1",
        "encrypted_content": "opaque-only-in-memory",
        "summary": [],
    }
    http = ScriptedHttp(
        [
            response(completed(reasoning, function())),
            response(
                completed(
                    function(
                        "call-2",
                        "get_filing_excerpt",
                        '{"filing_id":"0000012345-26-000000"}',
                    )
                )
            ),
            response(completed(message(json.dumps(final)))),
        ]
    )
    from investment_assistant.research_http import HttpResult

    sec_http = ScriptedHttp(
        [
            metadata(),
            submissions(),
            HttpResult(
                200, b"<p>Earnings were reported.</p>", {"content-type": "text/html"}
            ),
        ]
    )
    with SQLiteStorage(path) as storage:
        seed(storage)
        report = runner(storage, timer, http, sec_http)(EVENT, (SIGNAL,))
        assert storage.save_report_and_mark_reported(report, updated_at=NOW)
    requests = payloads(http)
    assert requests[-1]["tools"] == []  # one excerpt hits its own cap
    assert "opaque-only-in-memory" in json.dumps(requests[1])
    attempt = saved_attempt(path)
    assert attempt["status"] == "SUCCEEDED"
    assert attempt["usage"]["filing_list_calls"] == 1
    assert attempt["usage"]["filing_excerpt_calls"] == 1
    assert attempt["usage"]["sec_http_calls"] == 3
    assert len(attempt["tool_results"]) == 2
    assert "opaque-only-in-memory" not in json.dumps(attempt)
    assert "<p>" not in json.dumps(attempt)
    assert any(
        item["text"] == "Earnings were reported." for item in attempt["evidence"]
    )


def test_duplicates_and_invalid_calls_use_slots_but_do_not_repeat_http(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dedup.db"
    timer = Timer()
    calls = [
        function("first"),
        function("second", arguments=" { } "),
        function("invalid", "shell", '{"command":"never execute"}'),
        function("fourth"),
        function("fifth"),
        function("sixth"),
        function("seventh"),
    ]
    http = ScriptedHttp([response(completed(*calls)), response(completed(message()))])
    sec_http = ScriptedHttp([metadata(), submissions()])
    with SQLiteStorage(path) as storage:
        seed(storage)
        report = runner(storage, timer, http, sec_http)(EVENT, (SIGNAL,))
        assert _details(report).usage.tool_slots == 6
        assert _details(report).usage.filing_list_calls == 1
    assert len(sec_http.calls) == 2
    outputs = [
        item
        for item in payloads(http)[1]["input"]
        if item.get("type") == "function_call_output"
    ]
    assert len(outputs) == 7 and {item["call_id"] for item in outputs} == {
        c["call_id"] for c in calls
    }
    assert outputs[0]["output"] == outputs[1]["output"]
    assert json.loads(outputs[-1]["output"])["status"] == "limit"
    assert payloads(http)[1]["tools"] == []
    attempt = saved_attempt(path)
    assert len(attempt["tool_results"]) == 6
    assert "never execute" not in json.dumps(attempt)


def test_unavailable_tool_result_is_deduplicated_and_persisted(tmp_path: Path) -> None:
    path = tmp_path / "missing-sec.db"
    timer = Timer()
    http = ScriptedHttp(
        [
            response(completed(function(), function("second"))),
            response(completed(message())),
        ]
    )
    with SQLiteStorage(path) as storage:
        seed(storage)
        report = runner(storage, timer, http)(EVENT, (SIGNAL,))
        usage = _details(report).usage
        assert usage.filing_list_calls == 1 and usage.sec_http_calls == 0
    results = saved_attempt(path)["tool_results"]
    assert len(results) == 2 and results[0]["output"] == results[1]["output"]
    assert json.loads(results[0]["output"])["status"] == "unavailable"


def test_search_cap_forces_one_final_turn_and_denies_returned_edgar_calls(
    tmp_path: Path,
) -> None:
    http = ScriptedHttp(
        [
            response(
                completed(web(), web("open_page"), web("find_in_page"), function())
            ),
            response(completed(message())),
        ]
    )
    with SQLiteStorage(tmp_path / "search.db") as storage:
        seed(storage)
        report = runner(storage, Timer(), http)(EVENT, (SIGNAL,))
        usage = _details(report).usage
        assert usage.web_search_calls == 3
        assert usage.filing_list_calls == 0
    final = payloads(http)[1]
    assert final["tools"] == []
    assert json.loads(final["input"][-1]["output"])["status"] == "limit"


def test_valid_report_at_search_cap_stops_without_finalization(tmp_path: Path) -> None:
    http = ScriptedHttp(
        [response(completed(web(), web("open_page"), web("find_in_page"), message()))]
    )
    with SQLiteStorage(tmp_path / "cap.db") as storage:
        seed(storage)
        report = runner(storage, Timer(), http)(EVENT, (SIGNAL,))
        assert _details(report).usage.model_calls == 1
    assert len(http.calls) == 1


def test_search_allowance_shrinks_with_total_and_search_usage(tmp_path: Path) -> None:
    http = ScriptedHttp(
        [
            response(
                completed(
                    web(),
                    function("one", "invalid"),
                    function("two", "invalid"),
                    function("three", "invalid"),
                )
            ),
            response(completed(function("four", "invalid"))),
            response(completed(web(), message())),
        ]
    )
    with SQLiteStorage(tmp_path / "caps.db") as storage:
        seed(storage)
        runner(storage, Timer(), http)(EVENT, (SIGNAL,))
    assert [p["max_tool_calls"] for p in payloads(http)] == [3, 2, 1]


def test_four_tool_turns_then_exactly_one_finalization(tmp_path: Path) -> None:
    http = ScriptedHttp(
        [response(completed(function(str(i), "invalid"))) for i in range(4)]
        + [response(completed(message()))]
    )
    with SQLiteStorage(tmp_path / "rounds.db") as storage:
        seed(storage)
        report = runner(storage, Timer(), http)(EVENT, (SIGNAL,))
        assert report.details is not None
        assert report.details.usage.model_calls == 5
    assert [bool(p["tools"]) for p in payloads(http)] == [True, True, True, True, False]
    assert json.loads(payloads(http)[-1]["input"][-1]["output"])["status"] == "limit"


@pytest.mark.parametrize(
    "last",
    [
        completed(),
        completed(function("extra")),
        completed(message("invalid")),
        {"status": "incomplete"},
    ],
)
def test_invalid_final_turn_never_gets_an_extra_chance(
    tmp_path: Path, last: dict[str, Any]
) -> None:
    path = tmp_path / "bad-final.db"
    http = ScriptedHttp([response(completed(web(), web(), web())), response(last)])
    with SQLiteStorage(path) as storage:
        seed(storage)
        with pytest.raises(ResearchError):
            runner(storage, Timer(), http)(EVENT, (SIGNAL,))
        assert storage.list_reports(EVENT.event_id) == ()
        failed = storage.get_event(EVENT.event_id)
        assert failed is not None
        assert failed.status == EventStatus.FAILED
    assert len(http.calls) == 2 and saved_attempt(path)["status"] == "FAILED"
    assert saved_attempt(path)["retry_not_before"] == (
        NOW + timedelta(minutes=5)
    ).isoformat().replace("+00:00", "Z")


def test_provider_excess_searches_fail_without_executing_functions(
    tmp_path: Path,
) -> None:
    http = ScriptedHttp([response(completed(web(), web(), web(), web(), function()))])
    sec_http = ScriptedHttp([metadata(), submissions()])
    with SQLiteStorage(tmp_path / "excess.db") as storage:
        seed(storage)
        with pytest.raises(ResearchError):
            runner(storage, Timer(), http, sec_http)(EVENT, (SIGNAL,))
        assert not storage.list_reports(EVENT.event_id)
    assert len(http.calls) == 1 and not sec_http.calls


def test_unknown_citation_fails_with_no_report_or_notification(tmp_path: Path) -> None:
    http = ScriptedHttp(
        [response(completed(message(draft("invented").model_dump_json())))]
    )
    with SQLiteStorage(tmp_path / "unknown.db") as storage:
        seed(storage)
        EventManager(storage, clock=FixedClock(NOW)).process_pending(
            researcher=runner(storage, Timer(), http),
            notifier=lambda *args: pytest.fail("must not notify"),
        )
        assert storage.list_reports(EVENT.event_id) == ()
        failed = storage.get_event(EVENT.event_id)
        assert failed is not None
        assert failed.status == EventStatus.FAILED


def test_missing_model_and_budget_make_no_calls_and_no_additional_start(
    tmp_path: Path,
) -> None:
    http = ScriptedHttp([])
    with SQLiteStorage(tmp_path / "defer.db") as storage:
        seed(storage)
        with pytest.raises(ResearchDeferred):
            runner(storage, Timer(), http, key=False)(EVENT, (SIGNAL,))
        assert storage.research_starts_on(NOW) == 0
        for i in range(20):
            event = replace(EVENT, event_id=f"other-{i}")
            seed(storage, event)
            assert storage.reserve_research_attempt(
                event.event_id,
                1,
                now=NOW,
                model_version="test",
                prompt_version="test",
                has_api_key=True,
            )
        with pytest.raises(ResearchDeferred):
            runner(storage, Timer(), http)(EVENT, (SIGNAL,))
        assert storage.research_starts_on(NOW) == 20
    assert not http.calls


def test_deadline_includes_packet_assembly_and_final_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from investment_assistant import research

    timer = Timer()
    original = research.build_evidence_packet

    def slow_packet(
        storage: SQLiteStorage,
        event_id: str,
        event_update: int,
        *,
        as_of: datetime,
    ) -> EvidencePacket:
        packet = original(storage, event_id, event_update, as_of=as_of)
        timer.value += 91
        return packet

    with SQLiteStorage(tmp_path / "slow-packet.db") as storage:
        seed(storage)
        http = ScriptedHttp([])
        monkeypatch.setattr(research, "build_evidence_packet", slow_packet)
        with pytest.raises(ResearchError):
            runner(storage, timer, http)(EVENT, (SIGNAL,))
        assert not http.calls
    monkeypatch.setattr(research, "build_evidence_packet", original)
    timer = Timer()
    http = ScriptedHttp(
        [response(completed(web(), web(), web())), response(completed(message()))],
        timer,
        elapsed=46,
    )
    with SQLiteStorage(tmp_path / "slow-final.db") as storage:
        seed(storage)
        with pytest.raises(ResearchError):
            runner(storage, timer, http)(EVENT, (SIGNAL,))
        assert storage.list_reports(EVENT.event_id) == ()
    assert len(http.calls) == 2 and http.calls[-1][4] == 44


def test_stale_update_is_rejected_before_returning_report(tmp_path: Path) -> None:
    with SQLiteStorage(tmp_path / "stale.db") as storage:
        seed(storage)

        class UpdatingHttp(ScriptedHttp):
            def request(
                self,
                method: str,
                url: str,
                *,
                headers: Mapping[str, str],
                body: bytes | None,
                deadline: Deadline,
                timeout: float,
            ) -> HttpResult:
                storage.save_event(replace(EVENT, current_update=2))
                return super().request(
                    method,
                    url,
                    headers=headers,
                    body=body,
                    deadline=deadline,
                    timeout=timeout,
                )

        http = UpdatingHttp(
            [
                response(
                    completed(
                        message(),
                        usage={
                            "input_tokens": 80,
                            "output_tokens": 40,
                        },
                    )
                )
            ]
        )
        with pytest.raises(ResearchError):
            runner(storage, Timer(), http, budget_microdollars=2_000_000)(
                EVENT, (SIGNAL,)
            )
        queued = storage.get_event(EVENT.event_id)
        assert queued is not None
        assert queued.status == EventStatus.QUEUED
        assert not storage.list_reports(EVENT.event_id)
        assert storage.model_budget_used(NOW.date().isoformat()) == token_charge(
            RESEARCH_MODEL, 80, 40
        )


@pytest.mark.parametrize("combined", [False, True])
def test_significant_positive_news_runs_independently_or_with_market(
    tmp_path: Path, combined: bool
) -> None:
    with SQLiteStorage(tmp_path / "news.db") as storage:
        storage.initialize()
        manager = EventManager(storage, clock=FixedClock(NOW))
        item = article()
        result = classification(item)
        storage.save_news_article(item)
        storage.save_news_classification(result)
        signal = news_signal_from_classification(item, result)
        if combined:
            assert signal.direction is not None
            manager.handle_signal(replace(SIGNAL, direction=signal.direction))
        event = manager.handle_signal(signal).event
        assert event is not None
        values = draft(f"signal:{signal.signal_id}").model_dump(mode="json")
        values["cause_unknown"] = False
        values["likely_explanation"] = {
            "text": "The company published earnings.",
            "references": [f"signal:{signal.signal_id}", f"news:{item.article_id}"],
            "is_hypothesis": False,
        }
        http = ScriptedHttp([response(completed(message(json.dumps(values))))])
        delivered = []
        manager.process_pending(
            researcher=runner(storage, Timer(), http),
            notifier=lambda current, report: delivered.append(report),
        )
        assert len(delivered) == 1 and delivered[0].event_id == event.event_id
        assert len(storage.list_events()) == 1
        assert len(storage.list_signals(event.event_id)) == (2 if combined else 1)


def test_hosted_search_citations_are_saved_from_observed_metadata(
    tmp_path: Path,
) -> None:
    url = "https://example.test/earnings"
    data = draft().model_dump(mode="json")
    data["market_context"] = [
        {
            "text": "A release provides context.",
            "references": [url],
            "is_hypothesis": False,
        }
    ]
    http = ScriptedHttp(
        [
            response(
                completed(
                    web(sources=[{"url": url, "title": "Company release"}]),
                    message(json.dumps(data)),
                )
            )
        ]
    )
    with SQLiteStorage(tmp_path / "web.db") as storage:
        seed(storage)
        report = runner(storage, Timer(), http)(EVENT, (SIGNAL,))
        assert storage.save_report_and_mark_reported(report, updated_at=NOW)
        assert report.details is not None
        source = next(item for item in report.details.sources if item.kind == "web")
        assert source.identity == url and source.title == "Company release"
        attempt = storage.get_research_attempt(report.details.attempt_id)
        assert attempt is not None
        assert attempt.evidence[0].source == source


def test_required_continuation_overflow_stops_before_second_request(
    tmp_path: Path,
) -> None:
    reasoning = {
        "type": "reasoning",
        "id": "rs_1",
        "encrypted_content": "x" * 100001,
        "summary": [],
    }
    http = ScriptedHttp([response(completed(reasoning, function()))])
    with SQLiteStorage(tmp_path / "overflow.db") as storage:
        seed(storage)
        with pytest.raises(ResearchError):
            runner(storage, Timer(), http)(EVENT, (SIGNAL,))
        assert not storage.list_reports(EVENT.event_id)
    assert len(http.calls) == 1


def test_duplicate_unavailable_list_does_not_retry_http(tmp_path: Path) -> None:
    http = ScriptedHttp(
        [
            response(completed(function(), function("again"))),
            response(completed(message())),
        ]
    )
    sec_http = ScriptedHttp([RuntimeError("secret-provider-dump")])
    with SQLiteStorage(tmp_path / "failed-sec.db") as storage:
        seed(storage)
        report = runner(storage, Timer(), http, sec_http)(EVENT, (SIGNAL,))
        assert _details(report).usage.sec_http_calls == 1
    assert len(sec_http.calls) == 1
    assert "secret-provider-dump" not in json.dumps(payloads(http))


def test_empty_adapter_key_defers_without_reserving_a_start(tmp_path: Path) -> None:
    timer = Timer()
    http = ScriptedHttp([])
    with SQLiteStorage(tmp_path / "no-key.db") as storage:
        seed(storage)
        research = ResearchRunner(
            storage=storage,
            model=OpenAIResearchModel(http=http, clock=FixedClock(NOW), api_key=" "),
            sec=client(http, timer),
            clock=FixedClock(NOW),
            monotonic=timer,
        )
        with pytest.raises(ResearchDeferred):
            research(EVENT, (SIGNAL,))
        assert storage.research_starts_on(NOW) == 0
    assert not http.calls


def test_failed_sec_deadline_retains_executed_request_count(tmp_path: Path) -> None:
    path = tmp_path / "deadline-audit.db"
    timer = Timer()
    http = ScriptedHttp([response(completed(function()))])
    sec_http = ScriptedHttp([metadata()], timer, elapsed=91)
    with SQLiteStorage(path) as storage:
        seed(storage)
        with pytest.raises(ResearchError):
            runner(storage, timer, http, sec_http)(EVENT, (SIGNAL,))
    attempt = saved_attempt(path)
    assert attempt["status"] == "FAILED"
    assert attempt["usage"]["model_calls"] == 1
    assert attempt["usage"]["filing_list_calls"] == 1
    assert attempt["usage"]["sec_http_calls"] == 1
