"""CLI coverage for the human simulator command."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from aijournal.cli import app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_cli_ops_dev_human_simulator_runs_full_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")
    output = tmp_path / "human-sim"
    result = runner.invoke(
        app,
        [
            "ops",
            "dev",
            "human-sim",
            "--output",
            str(output),
            "--keep-workspace",
            "--max-stage",
            "8",
            "--pack-level",
            "L1",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "Result: PASS" in result.stdout
    assert output.exists()

    shutil.rmtree(output, ignore_errors=True)
