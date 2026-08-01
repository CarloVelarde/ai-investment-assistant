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

    settings = Settings()

    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.log_json is True
