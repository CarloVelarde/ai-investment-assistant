"""Guardrails that the live app stays single-process and adds no extra services."""

from pathlib import Path

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "src" / "investment_assistant"


def test_runtime_dependencies_do_not_add_live_or_distributed_services() -> None:
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    banned = (
        "alpaca",
        "celery",
        "discord",
        "httpx",
        "kafka",
        "openai",
        "redis",
        "sqlalchemy",
        "requests",
    )

    assert "pydantic-settings" in project
    for name in banned:
        assert name not in project.lower()


def test_source_tree_has_no_worker_orm_or_weekly_scheduler() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in SOURCE.rglob("*.py"))
    banned_tokens = ("Celery", "SQLAlchemy", "Redis", "Kafka", "APScheduler")

    assert "sqlite3" in text
    for token in banned_tokens:
        assert token not in text


def test_live_mode_adds_news_rest_without_websocket_discord_or_weekly_job() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in SOURCE.rglob("*.py"))

    assert "/v1beta1/news" in text
    assert "wss://stream.data.alpaca.markets/v1beta1/news" not in text
    assert "wss://stream.data.alpaca.markets/v2/" in text
    assert "discord" not in text.lower()
    assert "weekly" not in text.lower()
    assert "brokerage" not in text.lower()
    assert "place_order" not in text
    assert "class ResearchRunner" in text


def test_research_stays_inside_the_process_and_off_the_decision_path() -> None:
    """Live research is allowed. Later-milestone services and policy owners are not."""

    research_names = ("research.py", "research_model.py", "research_http.py", "sec.py")
    research_text = "\n".join(
        (SOURCE / name).read_text(encoding="utf-8") for name in research_names
    )
    assert "class ResearchRunner" in research_text
    assert "class OpenAIResearchModel" in research_text
    assert "class SecClient" in research_text
    assert "discord" not in research_text.lower()
    assert "wss://" not in research_text
    assert "place_order" not in research_text
    assert "Celery" not in research_text
    assert "APScheduler" not in research_text

    for name in ("detection.py", "market_detection.py", "news_classifier.py"):
        source = (SOURCE / name).read_text(encoding="utf-8")
        assert "ResearchRunner" not in source
        assert "EventManager" not in source
        assert "discord" not in source.lower()

    event_manager = (SOURCE / "event_manager.py").read_text(encoding="utf-8")
    assert "OpenAIResearchModel" not in event_manager
    assert "SecClient" not in event_manager
    assert "discord" not in event_manager.lower()
