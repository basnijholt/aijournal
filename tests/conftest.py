"""Shared pytest fixtures for CLI integration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from aijournal.cli import app

if TYPE_CHECKING:
    from pathlib import Path

_FIXED_NOW = datetime(2025, 2, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def cli_runner() -> CliRunner:
    """Return a Typer CliRunner for invoking the CLI."""
    return CliRunner()


@pytest.fixture
def cli_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_runner: CliRunner,
) -> Path:
    """Initialize a deterministic CLI workspace inside a temporary directory."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")
    monkeypatch.setattr("aijournal.utils.time.now", lambda: _FIXED_NOW)

    result = cli_runner.invoke(app, ["init"])
    if result.exit_code != 0:
        msg = f"Failed to initialize CLI workspace: {result.stdout}"
        raise RuntimeError(msg)

    return tmp_path
