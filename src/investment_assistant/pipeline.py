"""Synchronous offline walking-skeleton pipeline."""

from dataclasses import dataclass
from pathlib import Path

from investment_assistant.clock import Clock
from investment_assistant.correlation import correlate_signals
from investment_assistant.detection import detect_market_signal, detect_news_signal
from investment_assistant.fixture_readers import (
    load_market_fixture,
    load_news_fixture,
)
from investment_assistant.models import (
    MarketRecord,
    MarketSignal,
    NewsRecord,
    NewsSignal,
    ResearchReport,
    SignificantEvent,
)
from investment_assistant.reporting import (
    Notifier,
    Researcher,
    create_fake_research_report,
    emit_console_notification,
    research_and_notify,
)


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Observable results from one fixture-pair run."""

    market_record: MarketRecord
    news_record: NewsRecord
    market_signal: MarketSignal | None
    news_signal: NewsSignal | None
    event: SignificantEvent | None
    report: ResearchReport | None


def run_pipeline(
    *,
    tracked_symbol: str,
    market_fixture_path: Path,
    news_fixture_path: Path,
    clock: Clock,
    researcher: Researcher = create_fake_research_report,
    notifier: Notifier = emit_console_notification,
) -> PipelineResult:
    """Run one complete offline fixture scenario."""

    market_record = load_market_fixture(market_fixture_path)
    news_record = load_news_fixture(news_fixture_path)
    market_signal = detect_market_signal(
        market_record,
        tracked_symbol=tracked_symbol,
    )
    news_signal = detect_news_signal(
        news_record,
        tracked_symbol=tracked_symbol,
    )
    event = correlate_signals(market_signal, news_signal, clock=clock)
    report = research_and_notify(
        event,
        researcher=researcher,
        notifier=notifier,
    )

    return PipelineResult(
        market_record=market_record,
        news_record=news_record,
        market_signal=market_signal,
        news_signal=news_signal,
        event=event,
        report=report,
    )
