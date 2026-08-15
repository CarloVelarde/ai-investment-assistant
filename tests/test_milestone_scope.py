"""Guardrails that Milestone 4 stayed single-process and added no extra services."""

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


def test_live_mode_adds_no_news_client_discord_or_weekly_job() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in SOURCE.rglob("*.py"))

    assert "v1beta1/news" not in text
    assert "wss://stream.data.alpaca.markets/v2/" in text
    assert "discord" not in text.lower()
    assert "weekly" not in text.lower()
    assert "brokerage" not in text.lower()
    assert "place_order" not in text
