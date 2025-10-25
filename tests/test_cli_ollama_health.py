"""Tests for `aijournal ollama health`."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from aijournal.cli import app

runner = CliRunner()


def _has_ollama_health_command() -> bool:
    return any(cmd.name == "ollama" for cmd in app.registered_commands)


@pytest.fixture(autouse=True)
def skip_if_ollama_missing() -> None:
    if not _has_ollama_health_command():
        pytest.skip("ollama health command not available yet")


@pytest.fixture(autouse=True)
def fake_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)


def test_ollama_health_reports_models_and_default() -> None:
    result = runner.invoke(app, ["ollama", "health"])
    assert result.exit_code == 0, result.output
    normalized = result.output.lower()
    assert "models" in normalized
    assert "default" in normalized


def test_ollama_health_is_idempotent() -> None:
    first = runner.invoke(app, ["ollama", "health"])
    assert first.exit_code == 0, first.output

    second = runner.invoke(app, ["ollama", "health"])
    assert second.exit_code == 0, second.output
    assert first.output == second.output
