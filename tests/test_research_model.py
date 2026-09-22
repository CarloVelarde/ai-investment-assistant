"""Responses request, parsing, source, and stateless continuation tests."""

import json
from typing import Any

import pytest

from investment_assistant.clock import FixedClock
from investment_assistant.research_http import (
    MAX_RESPONSE_BYTES,
    Deadline,
    HttpResult,
    ResearchError,
)
from investment_assistant.research_model import (
    RESEARCH_MODEL,
    OpenAIResearchModel,
    research_request,
    web_reference,
)
from test_research_foundation import NOW, draft
from test_sec import ScriptedHttp, Timer, response


def message(
    text: str | None = None, annotations: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [
            {
                "type": "output_text",
                "text": text or draft().model_dump_json(),
                "annotations": annotations or [],
            }
        ],
    }


def function(
    call_id: str = "call-1", name: str = "get_recent_filings", arguments: str = "{}"
) -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


def web(
    kind: str = "search", sources: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    return {
        "type": "web_search_call",
        "id": "ws_1",
        "status": "completed",
        "action": {"type": kind, "sources": sources or []},
    }


def completed(*items: dict[str, Any], **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": "completed", "output": list(items)}
    payload.update(extra)
    return payload


def adapter(http: ScriptedHttp) -> OpenAIResearchModel:
    return OpenAIResearchModel(http=http, clock=FixedClock(NOW), api_key="test-secret")


def test_pinned_strict_request_and_finalization_without_tools() -> None:
    timer = Timer()
    http = ScriptedHttp(
        [
            response(
                completed(message(), usage={"input_tokens": 12, "output_tokens": 20})
            )
        ]
    )
    turn = adapter(http).respond(
        ({"role": "user", "content": "packet"},),
        search_cap=2,
        tools_enabled=True,
        deadline=Deadline.start(timer),
    )
    body = http.calls[0][3]
    assert body is not None
    payload = json.loads(body)
    assert payload["model"] == RESEARCH_MODEL == "gpt-5.4-mini-2026-03-17"
    assert payload["store"] is False and payload["max_output_tokens"] == 4000
    assert payload["max_tool_calls"] == 2
    assert [tool["type"] for tool in payload["tools"]] == [
        "web_search",
        "function",
        "function",
    ]
    assert payload["text"]["format"]["strict"]
    assert not payload["text"]["format"]["schema"]["additionalProperties"]
    assert "previous_response_id" not in payload
    assert "test-secret" not in json.dumps(payload)
    assert http.calls[0][2]["Authorization"] == "Bearer test-secret"
    assert (
        turn.draft == draft() and turn.input_tokens == 12 and turn.output_tokens == 20
    )
    final = research_request((), search_cap=0, tools_enabled=False)
    assert final["tools"] == [] and final["tool_choice"] == "none"
    assert "max_tool_calls" not in final


def test_all_items_parsed_and_opaque_reasoning_only_kept_in_memory() -> None:
    reasoning = {
        "type": "reasoning",
        "id": "rs_1",
        "encrypted_content": "opaque-private",
        "summary": [{"text": "never persist private reasoning"}],
    }
    http = ScriptedHttp(
        [
            response(
                completed(reasoning, web(), function(), message("Checking a filing."))
            )
        ]
    )
    turn = adapter(http).respond(
        (), search_cap=3, tools_enabled=True, deadline=Deadline.start(Timer())
    )
    assert len(turn.functions) == 1 and turn.web_calls == 1 and turn.draft is None
    assert turn.continuation[0]["encrypted_content"] == "opaque-private"
    assert turn.continuation[0]["summary"] == []
    assert "opaque-private" not in repr(turn)


def test_web_metadata_and_citations_normalize_without_invented_report_sources() -> None:
    url = "https://EXAMPLE.test/earnings#results"
    values = draft().model_dump(mode="json")
    values["market_context"] = [
        {
            "text": "An earnings release is available.",
            "references": [url],
            "is_hypothesis": False,
        }
    ]
    http = ScriptedHttp(
        [
            response(
                completed(
                    web("open_page", [{"url": url}]),
                    message(
                        json.dumps(values),
                        [{"type": "url_citation", "url": url, "title": "Earnings"}],
                    ),
                )
            )
        ]
    )
    turn = adapter(http).respond(
        (), search_cap=1, tools_enabled=True, deadline=Deadline.start(Timer())
    )
    assert len(turn.evidence) == 1
    assert turn.evidence[0].source.identity == "https://example.test/earnings"
    assert turn.draft is not None
    assert turn.draft.market_context[0].references == (
        web_reference("https://example.test/earnings"),
    )
    assert turn.evidence[0].source.retrieved_at == NOW


@pytest.mark.parametrize(
    "body",
    [
        {"status": "incomplete", "output": [message()]},
        completed(
            {
                "type": "message",
                "content": [{"type": "refusal", "refusal": "private text"}],
            }
        ),
        completed(message("not JSON")),
        completed(message(draft().model_dump_json()), message()),
        completed({"type": "computer_call"}),
        completed(function(), function()),
        completed(message(), usage={"output_tokens": 4001}),
        completed(message(), usage={"input_tokens": True}),
        completed({"type": "reasoning", "summary": []}),
    ],
)
def test_provider_failures_are_safe_and_never_retried(body: dict[str, Any]) -> None:
    http = ScriptedHttp([response(body)])
    with pytest.raises(ResearchError) as error:
        adapter(http).respond(
            (), search_cap=3, tools_enabled=True, deadline=Deadline.start(Timer())
        )
    assert "private" not in str(error.value) and "test-secret" not in str(error.value)
    assert len(http.calls) == 1


@pytest.mark.parametrize(
    "items", [[web(), web("find_in_page")], [web("open_page"), web()]]
)
def test_all_hosted_actions_count_against_requested_cap(
    items: list[dict[str, Any]],
) -> None:
    http = ScriptedHttp([response(completed(*items, function()))])
    with pytest.raises(ResearchError, match="search allowance"):
        adapter(http).respond(
            (), search_cap=1, tools_enabled=True, deadline=Deadline.start(Timer())
        )


@pytest.mark.parametrize(
    "result",
    [
        HttpResult(401, b"secret-provider-error"),
        HttpResult(200, b"x" * (MAX_RESPONSE_BYTES + 1)),
        HttpResult(200, b"{}", truncated=True),
        RuntimeError("test-secret"),
    ],
)
def test_transport_failure_and_response_size_are_bounded(
    result: HttpResult | Exception,
) -> None:
    http = ScriptedHttp([result])
    with pytest.raises(ResearchError) as error:
        adapter(http).respond(
            (), search_cap=1, tools_enabled=True, deadline=Deadline.start(Timer())
        )
    assert "test-secret" not in str(error.value) and "secret-provider" not in str(
        error.value
    )


def test_oversized_input_fails_before_http_and_late_output_is_rejected() -> None:
    http = ScriptedHttp([])
    with pytest.raises(ResearchError, match="size limit"):
        adapter(http).respond(
            ({"role": "user", "content": "x" * 100001},),
            search_cap=1,
            tools_enabled=True,
            deadline=Deadline.start(Timer()),
        )
    assert not http.calls
    timer = Timer()
    http = ScriptedHttp([response(completed(message()))], timer, elapsed=91)
    with pytest.raises(ResearchError):
        adapter(http).respond(
            (), search_cap=1, tools_enabled=True, deadline=Deadline.start(timer)
        )


def test_failed_search_cannot_supply_corroborating_sources() -> None:
    failed = web(sources=[{"url": "https://example.test/failed"}])
    failed["status"] = "failed"
    http = ScriptedHttp([response(completed(failed, message()))])
    turn = adapter(http).respond(
        (), search_cap=1, tools_enabled=True, deadline=Deadline.start(Timer())
    )
    assert turn.web_calls == 1 and not turn.evidence
