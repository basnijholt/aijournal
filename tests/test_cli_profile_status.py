"""Tests for `aijournal ops profile status` (command not yet implemented)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from aijournal.cli import app

if TYPE_CHECKING:
    from pathlib import Path

FIXED_NOW = datetime(2025, 2, 1, tzinfo=UTC)


def _has_profile_status() -> bool:
    result = CliRunner().invoke(app, ["ops", "profile", "status", "--help"])
    return result.exit_code == 0


@pytest.fixture(autouse=True)
def skip_if_missing() -> None:
    if not _has_profile_status():
        pytest.skip("profile status command not available yet")


@pytest.fixture(autouse=True)
def freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aijournal.utils.time.now", lambda: FIXED_NOW, raising=False)


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def _seed_profile(workspace: Path) -> None:
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
    type: preference
    subject: deep_work
    predicate: best_window
    value: "morning"
    statement: "Prefers morning deep work"
    scope:
      domain: work
      context: []
      conditions: []
    strength: 0.8
    status: accepted
    method: inferred
    user_verified: false
    review_after_days: 120
    provenance:
      sources:
        - entry_id: seed-entry
          spans: []
      first_seen: 2024-10-01
      last_updated: {stale}
"""

    _write_yaml(workspace / "profile" / "self_profile.yaml", self_profile)
    _write_yaml(workspace / "profile" / "claims.yaml", claims)


def _write_config(workspace: Path) -> None:
    config = """
impact_weights:
  values_goals: 2.0
  decision_style: 1.0
  affect_energy: 1.0
  traits: 0.5
  social: 0.5
"""

    _write_yaml(workspace / "config" / "config.yaml", config)


def _invoke(args: list[str], cli_runner: CliRunner) -> str:
    path = ["ops", "profile", *args[1:]] if args and args[0] == "profile" else args
    result = cli_runner.invoke(app, path)
    assert result.exit_code == 0, result.output
    return result.output


def test_profile_status_ranks_items(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _write_config(cli_workspace)

    output = _invoke(["profile", "status"], cli_runner)

    assert "values_motivations" in output
    assert "pref_mornings" in output
    assert output.index("values_motivations") < output.index("pref_mornings")


def test_profile_status_handles_missing_files(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    for rel_path in ("profile/self_profile.yaml", "profile/claims.yaml"):
        target = cli_workspace / rel_path
        if target.exists():
            target.unlink()

    result = cli_runner.invoke(app, ["ops", "profile", "status"])

    assert result.exit_code == 0
    assert "No profile data" in result.output


def test_profile_status_idempotent(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _write_config(cli_workspace)

    first = _invoke(["profile", "status"], cli_runner)
    second = _invoke(["profile", "status"], cli_runner)

    assert first == second
