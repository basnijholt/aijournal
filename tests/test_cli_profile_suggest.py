"""Tests for `aijournal profile suggest` using fake LLM mode."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app


runner = CliRunner()
DATE = "2025-02-03"
ENTRY_ID = "2025-02-03-sync-notes"


def _has_command() -> bool:
    # Typer adds nested commands as TyperCommand objects registered via add_typer
    return any(cmd.name == "profile" for cmd in app.registered_commands)


@pytest.fixture(autouse=True)
def skip_if_missing() -> None:
    if not _has_command():
        pytest.skip("profile commands not available yet")


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _seed_normalized(tmp_path: Path) -> None:
    normalized = {
        "id": ENTRY_ID,
        "created_at": f"{DATE}T09:00:00Z",
        "source_path": f"data/journal/2025/02/03/{ENTRY_ID}.md",
        "title": "Sync Notes",
        "tags": ["team"],
        "sections": [
            {"heading": "Monday Sync", "level": 1},
            {"heading": "Decisions", "level": 2},
        ],
    }
    _write_yaml(tmp_path / "data" / "normalized" / DATE / f"{ENTRY_ID}.yaml", normalized)


def _seed_profile(tmp_path: Path) -> None:
    self_profile = {
        "values_motivations": {
            "schwartz_top5": ["Universalism"],
            "last_updated": f"{DATE}T07:00:00Z",
            "review_after_days": 30,
        }
    }
    claims = {
        "claims": [
            {
                "id": "pref_focus",
                "statement": "Focus best before lunch",
                "status": "accepted",
                "confidence": 0.8,
                "last_updated": f"{DATE}T08:00:00Z",
                "review_after_days": 45,
            }
        ]
    }
    _write_yaml(tmp_path / "profile" / "self_profile.yaml", self_profile)
    _write_yaml(tmp_path / "profile" / "claims.yaml", claims)


def _invoke(tmp_path: Path) -> tuple[str, Path, int]:
    env = {"AIJOURNAL_FAKE_OLLAMA": "1"}
    args = ["profile", "suggest", "--date", DATE]
    result = runner.invoke(app, args, env=env)
    assert result.exit_code == 0, result.output
    path = tmp_path / "derived" / "profile_suggestions" / f"{DATE}.yaml"
    assert path.exists()
    folder = path.parent
    count = len(list(folder.glob("*.yaml")))
    return result.output, path, count


def test_profile_suggest_writes_suggestions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_normalized(tmp_path)
    _seed_profile(tmp_path)

    output, suggestions_path, _ = _invoke(tmp_path)
    assert str(suggestions_path) in output

    data = yaml.safe_load(suggestions_path.read_text(encoding="utf-8"))
    assert data.get("upserts") or data.get("updates"), "Expected suggested changes"
    meta = data.get("meta", {})
    for key in ("llm_model", "prompt_path", "prompt_hash", "created_at"):
        assert meta.get(key)


def test_profile_suggest_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_normalized(tmp_path)
    _seed_profile(tmp_path)

    _, suggestions_path, count_before = _invoke(tmp_path)
    mtime_before = suggestions_path.stat().st_mtime

    _, suggestions_path_again, count_after = _invoke(tmp_path)

    assert suggestions_path_again == suggestions_path
    assert count_before == count_after
    assert suggestions_path_again.stat().st_mtime == mtime_before
