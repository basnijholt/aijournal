"""Tests for `aijournal ollama health`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from aijournal.cli import app

if TYPE_CHECKING:
    from typer.testing import CliRunner


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


def test_ollama_health_reports_models_and_default(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(app, ["ops", "system", "ollama", "health"])
    assert result.exit_code == 0, result.output
    normalized = result.output.lower()
    assert "models" in normalized
    assert "default" in normalized


def test_ollama_health_is_idempotent(cli_runner: CliRunner) -> None:
    first = cli_runner.invoke(app, ["ops", "system", "ollama", "health"])
    assert first.exit_code == 0, first.output

    second = cli_runner.invoke(app, ["ops", "system", "ollama", "health"])
    assert second.exit_code == 0, second.output
    normalized_first = first.output.lower()
    normalized_second = second.output.lower()
    for token in ("models", "default"):
        assert token in normalized_first
        assert token in normalized_second
