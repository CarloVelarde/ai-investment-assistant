"""SEC evidence stays bounded, ticker-bound, and offline in tests."""

import json
from datetime import timedelta
from email.utils import format_datetime

import pytest

from investment_assistant.clock import FixedClock
from investment_assistant.research_http import (
    MAX_RESPONSE_BYTES,
    Deadline,
    HttpResult,
    ResearchTimeout,
)
from investment_assistant.sec import SecClient
from test_research_foundation import NOW


class Timer:
    def __init__(self) -> None:
        self.value = 0.0
        self.waits: list[float] = []

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.value += seconds


class ScriptedHttp:
    def __init__(
        self,
        responses: list[HttpResult | Exception],
        timer: Timer | None = None,
        elapsed: float = 0,
    ) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, str], bytes | None, float]] = []
        self.timer = timer
        self.elapsed = elapsed

    def request(self, method, url, *, headers, body, deadline, timeout):
        self.calls.append((method, url, dict(headers), body, timeout))
        if self.timer:
            self.timer.value += self.elapsed
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def response(data: object) -> HttpResult:
    return HttpResult(200, json.dumps(data).encode())


def metadata() -> HttpResult:
    return response({"0": {"ticker": "ACME", "cik_str": 12345}})


def submissions(count: int = 1) -> HttpResult:
    return response(
        {
            "filings": {
                "recent": {
                    "accessionNumber": [
                        f"0000012345-26-{index:06}" for index in range(count)
                    ],
                    "form": ["8-K"] * count,
                    "filingDate": ["2026-09-16"] * count,
                    "primaryDocument": [f"filing{index}.htm" for index in range(count)],
                },
                "files": [{"name": "must-not-traverse.json"}],
            }
        }
    )


def client(
    http: ScriptedHttp, timer: Timer, agent: str = "Assistant test@example.test"
) -> SecClient:
    return SecClient(
        http=http,
        clock=FixedClock(NOW),
        user_agent=agent,
        monotonic=timer,
        sleep=timer.sleep,
    )


def test_ticker_cache_bounded_list_excerpt_and_run_scoped_ids() -> None:
    timer = Timer()
    http = ScriptedHttp(
        [
            metadata(),
            submissions(14),
            HttpResult(
                200,
                b"<html><style>hidden</style><script>do evil</script><p>Company &amp; earnings</p></html>",
                {"content-type": "text/html"},
            ),
            submissions(),
        ]
    )
    sec = client(http, timer)
    session = sec.start_run("ACME", NOW)
    deadline = Deadline.start(timer)
    listed = session.get_recent_filings(deadline)
    filings = json.loads(listed.text)["filings"]
    assert len(filings) == 10
    assert filings[0]["filing_id"] == "0000012345-26-000013"
    excerpt = session.get_filing_excerpt(filings[0]["filing_id"], deadline)
    assert excerpt.evidence[0].text == "Company & earnings"
    assert "do evil" not in excerpt.text and "hidden" not in excerpt.text
    assert (
        http.calls[2][1]
        == "https://www.sec.gov/Archives/edgar/data/12345/000001234526000013/filing13.htm"
    )
    assert session.http_calls == 3
    other = sec.start_run("ACME", NOW)
    assert not other.get_filing_excerpt(filings[0]["filing_id"], deadline).evidence
    other.get_recent_filings(deadline)
    assert len(http.calls) == 4  # no second metadata request
    assert timer.waits == [0.5, 0.5, 0.5]
    assert all(
        call[2]["User-Agent"] == "Assistant test@example.test" for call in http.calls
    )
    assert all(call[4] <= 10 for call in http.calls)


@pytest.mark.parametrize("agent", ["", " ", "bad\r\nInjected: value"])
def test_missing_or_invalid_user_agent_never_calls_http(agent: str) -> None:
    timer = Timer()
    http = ScriptedHttp([])
    assert (
        not client(http, timer, agent)
        .start_run("ACME", NOW)
        .get_recent_filings(Deadline.start(timer))
        .evidence
    )
    assert http.calls == []


@pytest.mark.parametrize(
    "result",
    [
        HttpResult(403, b"private provider message"),
        HttpResult(302, b"", {"location": "https://evil.test"}),
        HttpResult(200, b"{}", truncated=True),
        HttpResult(200, b"not json"),
        HttpResult(200, b"x" * (MAX_RESPONSE_BYTES + 1)),
        RuntimeError("secret key"),
    ],
)
def test_bad_metadata_is_safe_unavailable_without_retry(result) -> None:
    timer = Timer()
    http = ScriptedHttp([result])
    output = (
        client(http, timer)
        .start_run("ACME", NOW)
        .get_recent_filings(Deadline.start(timer))
    )
    assert json.loads(output.text)["status"] == "unavailable"
    assert "secret" not in output.text and "private" not in output.text
    assert len(http.calls) == 1


@pytest.mark.parametrize(
    "retry_after,expected",
    [
        (None, 60),
        ("120", 120),
        (format_datetime(NOW + timedelta(seconds=75), usegmt=True), 75),
        ("invalid", 60),
    ],
)
def test_rate_limit_disables_later_runs_until_retry_after(
    retry_after, expected
) -> None:
    timer = Timer()
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    http = ScriptedHttp([HttpResult(429, b"", headers), metadata(), submissions()])
    sec = client(http, timer)
    sec.start_run("ACME", NOW).get_recent_filings(Deadline.start(timer))
    timer.value = expected - 1
    sec.start_run("ACME", NOW).get_recent_filings(Deadline.start(timer))
    assert len(http.calls) == 1
    timer.value = expected
    assert sec.start_run("ACME", NOW).get_recent_filings(Deadline.start(timer)).evidence
    assert len(http.calls) == 3


def test_filters_forms_dates_paths_and_unknown_company() -> None:
    timer = Timer()
    raw = json.loads(submissions(5).body)
    recent = raw["filings"]["recent"]
    recent["form"][0] = "4"
    recent["filingDate"][1] = "2020-01-01"
    recent["filingDate"][2] = "2026-09-18"
    recent["primaryDocument"][3] = "../../elsewhere.htm"
    http = ScriptedHttp([metadata(), response(raw)])
    sec = client(http, timer)
    assert (
        not sec.start_run("OTHER", NOW)
        .get_recent_filings(Deadline.start(timer))
        .evidence
    )
    result = sec.start_run("ACME", NOW).get_recent_filings(Deadline.start(timer))
    assert len(result.evidence) == 1
    assert len(http.calls) == 2


def test_arbitrary_filing_targets_cannot_cause_http() -> None:
    timer = Timer()
    http = ScriptedHttp([metadata(), submissions()])
    run = client(http, timer).start_run("ACME", NOW)
    run.get_recent_filings(Deadline.start(timer))
    for target in ("https://evil.test", "../secret", "0000012345-26-999999"):
        assert not run.get_filing_excerpt(target, Deadline.start(timer)).evidence
    assert len(http.calls) == 2


def test_large_filing_returns_labeled_bounded_partial_and_unsupported_content_is_unavailable() -> (
    None
):
    timer = Timer()
    http = ScriptedHttp(
        [
            metadata(),
            submissions(),
            HttpResult(
                200,
                b"<p>" + b"x" * 30000,
                {"content-type": "text/html"},
                truncated=True,
            ),
            HttpResult(200, b"%PDF", {"content-type": "application/pdf"}),
        ]
    )
    run = client(http, timer).start_run("ACME", NOW)
    run.get_recent_filings(Deadline.start(timer))
    result = run.get_filing_excerpt("0000012345-26-000000", Deadline.start(timer))
    assert len(result.text) <= 8000 and result.evidence[0].truncated
    assert "<p>" not in result.text
    assert not run.get_filing_excerpt(
        "0000012345-26-000000", Deadline.start(timer)
    ).evidence


def test_late_http_and_pacing_reject_without_another_call() -> None:
    timer = Timer()
    http = ScriptedHttp([metadata()], timer, elapsed=91)
    with pytest.raises(ResearchTimeout):
        client(http, timer).start_run("ACME", NOW).get_recent_filings(
            Deadline.start(timer)
        )
    assert len(http.calls) == 1
    timer = Timer()
    http = ScriptedHttp([metadata()])
    assert (
        not client(http, timer)
        .start_run("ACME", NOW)
        .get_recent_filings(Deadline(0.25, timer))
        .evidence
    )
    assert len(http.calls) == 1


def test_per_request_timeout_can_degrade_before_run_deadline() -> None:
    timer = Timer()
    http = ScriptedHttp([ResearchTimeout("safe timeout")], timer, elapsed=10)
    assert (
        not client(http, timer)
        .start_run("ACME", NOW)
        .get_recent_filings(Deadline.start(timer))
        .evidence
    )
    assert len(http.calls) == 1
