"""Tests for `aijournal facts` using fake Ollama outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app


runner = CliRunner()
DATE = "2025-02-03"
ENTRY_ID = "2025-02-03-sync-notes"


def _has_command(name: str) -> bool:
    return any(info.name == name for info in app.registered_commands)


@pytest.fixture(autouse=True)
def skip_if_missing() -> None:
    if not _has_command("facts"):
        pytest.skip("facts command not available yet")


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


def _read_yaml(path: Path) -> Dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_facts_generates_microfacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_normalized(tmp_path)

    env = {"AIJOURNAL_FAKE_OLLAMA": "1"}
    result = runner.invoke(app, ["facts", "--date", DATE], env=env)

    assert result.exit_code == 0, result.stdout

    facts_path = tmp_path / "derived" / "microfacts" / f"{DATE}.yaml"
    assert facts_path.exists()

    data = _read_yaml(facts_path)
    facts = data.get("facts", [])
    assert isinstance(facts, list)
    if facts:
        first = facts[0]
        assert first.get("id") and first.get("statement")
    meta = data.get("meta", {})
    for key in ("llm_model", "prompt_path", "prompt_hash", "created_at"):
        assert meta.get(key), f"Missing {key}"
    assert str(facts_path) in result.stdout


def test_facts_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_normalized(tmp_path)

    env = {"AIJOURNAL_FAKE_OLLAMA": "1"}
    first = runner.invoke(app, ["facts", "--date", DATE], env=env)
    assert first.exit_code == 0

    facts_path = tmp_path / "derived" / "microfacts" / f"{DATE}.yaml"
    before = facts_path.stat().st_mtime

    second = runner.invoke(app, ["facts", "--date", DATE], env=env)
    assert second.exit_code == 0
    after = facts_path.stat().st_mtime

    assert before == after
