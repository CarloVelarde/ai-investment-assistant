"""Tests for application configuration."""

from pathlib import Path

import pytest

from investment_assistant.config import Settings


def test_default_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    monkeypatch.delenv("INVESTMENT_ASSISTANT_ENVIRONMENT", raising=False)
    monkeypatch.delenv("INVESTMENT_ASSISTANT_LOG_LEVEL", raising=False)
    monkeypatch.delenv("INVESTMENT_ASSISTANT_LOG_JSON", raising=False)
    monkeypatch.delenv("INVESTMENT_ASSISTANT_DATABASE_PATH", raising=False)

    settings = Settings()

    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.log_json is True
    assert settings.database_path == Path("investment_assistant.db")


def test_database_path_can_be_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    monkeypatch.setenv(
        "INVESTMENT_ASSISTANT_DATABASE_PATH",
        str(database_path),
    )

    assert Settings().database_path == database_path
