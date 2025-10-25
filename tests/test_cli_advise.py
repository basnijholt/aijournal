"""Tests for `aijournal advise` (fake LLM mode)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()
DATE = "2025-02-03"


def _has_command(name: str) -> bool:
    return any(info.name == name for info in app.registered_commands)


@pytest.fixture(autouse=True)
def skip_if_missing() -> None:
    if not _has_command("advise"):
        pytest.skip("advise command not available yet")


@pytest.fixture(autouse=True)
def freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aijournal.cli._now",
        lambda: datetime(2025, 2, 3, 10, 0, tzinfo=UTC),
        raising=False,
    )


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def _seed_profile(tmp_path: Path) -> None:
    self_profile = """
coaching_prefs:
  tone: "direct, warm"
  depth: "concrete first"
boundaries_ethics:
  red_lines:
    - "No health advice"
"""
    claims = """
claims:
  - id: pref_focus
    statement: "Focus best before lunch"
    status: accepted
    confidence: 0.8
"""
    _write_yaml(tmp_path / "profile" / "self_profile.yaml", self_profile)
    _write_yaml(tmp_path / "profile" / "claims.yaml", claims)


def _invoke(tmp_path: Path) -> tuple[str, Path, int]:
    env = {"AIJOURNAL_FAKE_OLLAMA": "1"}
    result = runner.invoke(app, ["advise", "How to plan next week?"], env=env)
    assert result.exit_code == 0, result.output
    folder = tmp_path / "derived" / "advice" / DATE
    files = sorted(folder.glob("*.yaml"))
    assert files, "No advice file generated"
    return result.output, files[0], len(files)


def test_advise_generates_advice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)

    output, advice_file, _count = _invoke(tmp_path)
    assert str(advice_file) in output

    data = yaml.safe_load(advice_file.read_text(encoding="utf-8"))
    assert isinstance(data.get("recommendations"), list)
    assert data.get("alignment")
    meta = data.get("meta", {})
    for key in ("llm_model", "prompt_path", "prompt_hash", "created_at"):
        assert meta.get(key)


def test_advise_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)

    output1, advice_file, count1 = _invoke(tmp_path)
    before = advice_file.stat().st_mtime

    output2, advice_file_again, count2 = _invoke(tmp_path)
    assert advice_file_again == advice_file
    assert count1 == count2
    assert advice_file_again.stat().st_mtime == before
    assert str(advice_file_again) in output1
    assert str(advice_file_again) in output2
