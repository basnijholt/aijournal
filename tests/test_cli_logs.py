"""Tests for `aijournal ops logs tail`."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from aijournal.cli import app

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner


def _write_trace(workspace: Path, entries: list[dict[str, object]]) -> Path:
    log_path = workspace / "derived" / "logs" / "run_trace.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return log_path


def test_logs_tail_missing_file(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    log_path = cli_workspace / "derived" / "logs" / "run_trace.jsonl"
    if log_path.exists():
        log_path.unlink()
    if log_path.parent.exists() and not any(log_path.parent.iterdir()):
        log_path.parent.rmdir()
    result = cli_runner.invoke(app, ["ops", "logs", "tail"])

    assert result.exit_code == 1
    message = (result.stderr or "") + (result.stdout or "")
    assert "No run trace log found" in message


def test_logs_tail_formatted_output(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _write_trace(
        cli_workspace,
        [
            {
                "timestamp": "2025-10-30T18:40:00Z",
                "run_id": "summarize-1",
                "command": "summarize",
                "event": "command_start",
            },
            {
                "timestamp": "2025-10-30T18:40:01Z",
                "run_id": "summarize-1",
                "command": "summarize",
                "event": "end",
                "step": "persist_output",
                "duration_ms": 42.5,
            },
        ],
    )

    result = cli_runner.invoke(app, ["ops", "logs", "tail", "--last", "1"])

    assert result.exit_code == 0
    output = result.stdout.strip()
    assert "summarize" in output
    assert "persist_output" in output
    assert "42.5ms" in output


def test_logs_tail_raw_mode(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    entries = [
        {
            "timestamp": "2025-10-30T19:00:00Z",
            "run_id": "advise-1",
            "command": "advise",
            "event": "command_start",
        },
    ]
    log_path = _write_trace(cli_workspace, entries)

    result = cli_runner.invoke(
        app,
        [
            "ops",
            "logs",
            "tail",
            "--last",
            "1",
            "--raw",
        ],
    )

    assert result.exit_code == 0
    expected_line = json.dumps(entries[-1], ensure_ascii=False)
    assert expected_line in result.stdout
    assert log_path.exists()
