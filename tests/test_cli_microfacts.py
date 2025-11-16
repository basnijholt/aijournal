"""Tests for microfacts ops commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from aijournal.cli import app
from tests.test_cli_facts import DATE, _write_normalized, _write_summary

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner


def _load_yaml(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_microfacts_rebuild_command_writes_artifacts(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _write_normalized(cli_workspace)
    _write_summary(cli_workspace)

    # Generate daily microfacts first.
    first = cli_runner.invoke(
        app,
        ["ops", "pipeline", "extract-facts", "--date", DATE],
    )
    assert first.exit_code == 0, first.stdout

    result = cli_runner.invoke(app, ["ops", "microfacts", "rebuild"])

    assert result.exit_code == 0, result.stdout
    derived = cli_workspace / "derived" / "microfacts"
    consolidated = derived / "consolidated.yaml"
    assert consolidated.exists()
    consolidated_artifact = _load_yaml(consolidated)
    assert consolidated_artifact.get("kind") == "microfacts.consolidated"
    data = consolidated_artifact.get("data", {})
    assert data.get("facts") or [], "Expected consolidated facts"

    logs_dir = derived / "logs"
    log_files = sorted(logs_dir.glob("rebuild-*.yaml"))
    assert log_files, "Expected a consolidation log file"
    log_payload = _load_yaml(log_files[-1])
    assert log_payload.get("kind") == "microfacts.log"
    log_entries = log_payload.get("data", {}).get("entries") or []
    assert log_entries, "Expected log entries in consolidation log"
