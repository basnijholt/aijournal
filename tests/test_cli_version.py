"""Smoke test for the `aij version` command."""

from __future__ import annotations

from typer.testing import CliRunner

from aijournal import _version
from aijournal.cli import app


def test_version_command(monkeypatch: CliRunner) -> None:
    # Force a deterministic version so we can assert the output
    monkeypatch.setattr(_version, "__version__", "9.3.1-test")
    runner = CliRunner()

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert "aijournal version: 9.3.1-test" in result.stdout
    assert "source root:" in result.stdout
