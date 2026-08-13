"""Guardrails that Milestone 3 stayed local, offline, and single-process."""

from pathlib import Path

ROOT = Path(__file__).parents[1]


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
    source = ROOT / "src" / "investment_assistant"
    text = "\n".join(path.read_text(encoding="utf-8") for path in source.rglob("*.py"))
    banned_tokens = ("Celery", "SQLAlchemy", "Redis", "Kafka", "APScheduler")

    assert "sqlite3" in text
    for token in banned_tokens:
        assert token not in text
