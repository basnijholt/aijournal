"""Tests for `aijournal profile status` (command not yet implemented)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from aijournal.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()
FIXED_NOW = datetime(2025, 2, 1, tzinfo=UTC)


def _has_profile_status() -> bool:
    return any(cmd.name == "profile-status" for cmd in app.registered_commands)


@pytest.fixture(autouse=True)
def skip_if_missing() -> None:
    if not _has_profile_status():
        pytest.skip("profile status command not available yet")


@pytest.fixture(autouse=True)
def freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aijournal.cli._now", lambda: FIXED_NOW, raising=False)


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def _seed_profile(tmp_path: Path) -> None:
    # Two facets and one claim with different staleness to test ordering
    now = FIXED_NOW
    stale = (now - timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    self_profile = f"""
traits:
  big_five:
    openness:
      score: 0.7
      method: self_report
      user_verified: true
      last_updated: {fresh}
      review_after_days: 60
values_motivations:
  schwartz_top5:
    - Universalism
  last_updated: {stale}
  review_after_days: 30
"""

    claims = f"""
claims:
  - id: pref_mornings
    statement: "Prefers morning deep work"
    status: accepted
    confidence: 0.8
    freshness: 0.5
    last_updated: {stale}
    review_after_days: 120
"""

    _write_yaml(tmp_path / "profile" / "self_profile.yaml", self_profile)
    _write_yaml(tmp_path / "profile" / "claims.yaml", claims)


def _write_config(tmp_path: Path) -> None:
    config = """
impact_weights:
  values_goals: 2.0
  decision_style: 1.0
  affect_energy: 1.0
  traits: 0.5
  social: 0.5
"""

    _write_yaml(tmp_path / "config" / "config.yaml", config)


def _invoke(tmp_path: Path, args: list[str]) -> str:
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result.output


def test_profile_status_ranks_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    _write_config(tmp_path)

    output = _invoke(tmp_path, ["profile", "status"])

    assert "values_motivations" in output
    assert "pref_mornings" in output
    assert output.index("values_motivations") < output.index("pref_mornings")


def test_profile_status_handles_missing_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["profile", "status"])

    assert result.exit_code == 0
    assert "No profile data" in result.output


def test_profile_status_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    _write_config(tmp_path)

    first = _invoke(tmp_path, ["profile", "status"])
    second = _invoke(tmp_path, ["profile", "status"])

    assert first == second
