"""Tests for `aijournal pack` CLI."""

from __future__ import annotations

from pathlib import Path

import json
import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app


runner = CliRunner()
DATE = "2025-02-03"
ENTRY_ID = "2025-02-03-sync-notes"


def _has_pack_command() -> bool:
    return any(cmd.name == "pack" for cmd in app.registered_commands)


@pytest.fixture(autouse=True)
def skip_if_missing() -> None:
    if not _has_pack_command():
        pytest.skip("pack command not available yet")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def _seed_profile(tmp_path: Path) -> None:
    _write(
        tmp_path / "profile" / "self_profile.yaml",
        """
traits:
  big_five:
    openness: {score: 0.7}
""",
    )
    _write(
        tmp_path / "profile" / "claims.yaml",
        """
claims:
  - id: pref_focus
    statement: "Focus best before lunch"
""",
    )


def _seed_daily_artifacts(tmp_path: Path) -> None:
    _write(
        tmp_path / "data" / "normalized" / DATE / f"{ENTRY_ID}.yaml",
        "id: 2025-02-03-sync-notes\ncreated_at: 2025-02-03T09:00:00Z\ntitle: Sync Notes",
    )
    _write(
        tmp_path / "derived" / "summaries" / f"{DATE}.yaml",
        "day: 2025-02-03\nbullets:\n  - planning",
    )
    _write(
        tmp_path / "derived" / "microfacts" / f"{DATE}.yaml",
        "facts:\n  - id: fact1\n    statement: Prefers mornings",
    )


def test_pack_l1_includes_profile_and_claims(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)

    result = runner.invoke(app, ["pack", "--level", "L1", "--format", "yaml"])
    assert result.exit_code == 0, result.output
    assert "traits" in result.output
    assert "claims:" in result.output


def test_pack_l2_includes_daily_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    _seed_daily_artifacts(tmp_path)

    result = runner.invoke(app, ["pack", "--level", "L2", "--date", DATE])
    assert result.exit_code == 0
    assert "normalized" in result.output or ENTRY_ID in result.output
    assert "summaries" in result.output


def test_pack_missing_profile_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["pack", "--level", "L1"])
    assert result.exit_code != 0
    assert "self_profile" in result.output.lower()


def test_pack_trims_to_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    big_text = "sentence " * 500
    _write(tmp_path / "data" / "normalized" / DATE / "big.yaml", big_text)

    result = runner.invoke(
        app,
        ["pack", "--level", "L2", "--date", DATE, "--max-tokens", "50"],
    )
    assert result.exit_code == 0
    assert "trimmed" in result.output.lower()


def test_pack_output_file_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    out_path = tmp_path / "derived" / "packs" / "l1.yaml"

    first = runner.invoke(app, ["pack", "--level", "L1", "--output", str(out_path)])
    assert first.exit_code == 0
    mtime = out_path.stat().st_mtime

    second = runner.invoke(app, ["pack", "--level", "L1", "--output", str(out_path)])
    assert second.exit_code == 0
    assert out_path.stat().st_mtime == mtime


def test_pack_dry_run_lists_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    _seed_daily_artifacts(tmp_path)

    result = runner.invoke(app, ["pack", "--level", "L2", "--dry-run"])
    assert result.exit_code == 0
    assert "profile/self_profile.yaml" in result.output
    assert "normalized" in result.output


def test_pack_deterministic_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    _seed_daily_artifacts(tmp_path)
    _write(tmp_path / "derived" / "advice" / DATE / "adv_a.yaml", "{}")
    _write(tmp_path / "derived" / "advice" / DATE / "adv_b.yaml", "{}")

    first = runner.invoke(app, ["pack", "--level", "L3", "--date", DATE])
    second = runner.invoke(app, ["pack", "--level", "L3", "--date", DATE])
    assert first.output == second.output


def test_pack_json_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)

    result = runner.invoke(app, ["pack", "--level", "L1", "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["level"] == "L1"
