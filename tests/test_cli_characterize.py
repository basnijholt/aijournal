"""Tests for characterize/review-updates commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from typer.testing import CliRunner

from aijournal.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()
DATE = "2025-02-03"
ENTRY_ID = "2025-02-03-focus-notes"
SOURCE_HASH = "abc123hash"


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _seed_normalized(tmp_path: Path) -> None:
    normalized = {
        "id": ENTRY_ID,
        "created_at": f"{DATE}T09:13:00Z",
        "source_path": f"data/journal/2025/02/03/{ENTRY_ID}.md",
        "title": "Focus Notes",
        "tags": ["focus", "planning"],
        "sections": [
            {"heading": "Morning Focus", "level": 1},
            {"heading": "Decisions", "level": 2},
        ],
        "source_hash": SOURCE_HASH,
    }
    _write_yaml(tmp_path / "data" / "normalized" / DATE / f"{ENTRY_ID}.yaml", normalized)


def _seed_manifest(tmp_path: Path) -> None:
    manifest = [
        {
            "hash": SOURCE_HASH,
            "path": f"data/journal/2025/02/03/{ENTRY_ID}.md",
            "normalized": f"data/normalized/{DATE}/{ENTRY_ID}.yaml",
            "source_type": "journal",
            "ingested_at": f"{DATE}T10:00:00Z",
            "created_at": f"{DATE}T09:13:00Z",
            "id": ENTRY_ID,
            "tags": ["focus"],
            "model": "fake-ollama",
        },
    ]
    _write_yaml(tmp_path / "data" / "manifest" / "ingested.yaml", manifest)


def _seed_profile(tmp_path: Path) -> None:
    profile = {
        "values_motivations": {
            "schwartz_top5": ["Self-Direction"],
            "review_after_days": 60,
            "last_updated": f"{DATE}T07:00:00Z",
        },
    }
    claims = {"claims": []}
    _write_yaml(tmp_path / "profile" / "self_profile.yaml", profile)
    _write_yaml(tmp_path / "profile" / "claims.yaml", claims)


def _run_characterize(tmp_path: Path) -> Path:
    env = {"AIJOURNAL_FAKE_OLLAMA": "1"}
    result = runner.invoke(app, ["characterize", "--date", DATE], env=env)
    assert result.exit_code == 0, result.output
    pending_dir = tmp_path / "derived" / "pending" / "profile_updates"
    batches = sorted(pending_dir.glob("*.yaml"))
    assert batches, "Expected pending batch"
    return batches[-1]


def test_characterize_generates_pending_batch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_normalized(tmp_path)
    _seed_manifest(tmp_path)
    _seed_profile(tmp_path)

    batch_path = _run_characterize(tmp_path)
    data = yaml.safe_load(batch_path.read_text(encoding="utf-8"))

    assert data.get("inputs")
    assert data.get("meta", {}).get("prompt_path") == "prompts/characterize.md"
    proposals = data.get("proposals", {})
    claims = proposals.get("claims")
    assert claims, "Expected at least one claim proposal"
    first_claim = claims[0]
    assert SOURCE_HASH in (first_claim.get("evidence_hashes") or [])


def test_review_updates_applies_batch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_normalized(tmp_path)
    _seed_manifest(tmp_path)
    _seed_profile(tmp_path)

    batch_path = _run_characterize(tmp_path)
    env = {"AIJOURNAL_FAKE_OLLAMA": "1"}
    result = runner.invoke(
        app,
        ["review-updates", "--file", str(batch_path), "--apply"],
        env=env,
    )
    assert result.exit_code == 0, result.output

    profile_path = tmp_path / "profile" / "self_profile.yaml"
    claims_path = tmp_path / "profile" / "claims.yaml"
    profile_data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    claims_data = yaml.safe_load(claims_path.read_text(encoding="utf-8"))

    assert profile_data.get("values_motivations", {}).get("recurring_theme")
    assert claims_data.get("claims"), "Expected claim upsert applied"
