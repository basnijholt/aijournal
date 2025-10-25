"""Tests for `aijournal summarize` using fake Ollama outputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()
DATE = "2025-02-03"
ENTRY_ID = "2025-02-03-sync-notes"


def _has_command(name: str) -> bool:
    return any(info.name == name for info in app.registered_commands)


@pytest.fixture(autouse=True)
def skip_if_missing() -> None:
    if not _has_command("summarize"):
        pytest.skip("summarize command not available yet")


def _write_normalized(tmp_path: Path) -> Path:
    normalized = tmp_path / "data" / "normalized" / DATE / f"{ENTRY_ID}.yaml"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    normalized.write_text(
        yaml.safe_dump(
            {
                "id": ENTRY_ID,
                "created_at": "2025-02-03T14:05:00Z",
                "source_path": f"data/journal/2025/02/03/{ENTRY_ID}.md",
                "title": "Sync Notes",
                "tags": ["team"],
                "sections": [
                    {"heading": "Monday Sync", "level": 1},
                    {"heading": "Decisions", "level": 2},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return normalized


def _read_yaml(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_summarize_generates_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_normalized(tmp_path)

    env = {"AIJOURNAL_FAKE_OLLAMA": "1"}
    result = runner.invoke(app, ["summarize", "--date", DATE], env=env)

    assert result.exit_code == 0, result.stdout

    summary_path = tmp_path / "derived" / "summaries" / f"{DATE}.yaml"
    assert summary_path.exists()

    data = _read_yaml(summary_path)
    assert data.get("day") == DATE
    assert isinstance(data.get("highlights"), list)
    assert isinstance(data.get("todo_candidates"), list)
    meta = data.get("meta", {})
    for key in ("llm_model", "prompt_path", "prompt_hash", "created_at"):
        assert meta.get(key), f"Missing {key}"
    assert str(summary_path) in result.stdout


def test_summarize_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_normalized(tmp_path)

    env = {"AIJOURNAL_FAKE_OLLAMA": "1"}
    first = runner.invoke(app, ["summarize", "--date", DATE], env=env)
    assert first.exit_code == 0

    summary_path = tmp_path / "derived" / "summaries" / f"{DATE}.yaml"
    before = summary_path.stat().st_mtime

    second = runner.invoke(app, ["summarize", "--date", DATE], env=env)
    assert second.exit_code == 0
    after = summary_path.stat().st_mtime

    assert before == after
