"""Tests for the unified profile update command."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from aijournal.cli import app
from aijournal.commands import profile_update as profile_update_module
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.evidence import SourceRef
from aijournal.domain.facts import DailySummary, MicroFact, MicroFactsFile
from aijournal.domain.prompts import PromptProfileUpdates
from aijournal.io.artifacts import save_artifact
from aijournal.io.yaml_io import dump_yaml

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from typer.testing import CliRunner

DATE = "2025-02-03"
ENTRY_ID = "2025-02-03-focus-notes"
SOURCE_HASH = "abc123hash"


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(payload, sort_keys=False), encoding="utf-8")


def _seed_normalized(
    tmp_path: Path,
    *,
    entry_id: str = ENTRY_ID,
    date: str = DATE,
    source_hash: str = SOURCE_HASH,
    title: str | None = None,
    tags: list[str] | None = None,
) -> None:
    normalized = {
        "id": entry_id,
        "created_at": f"{date}T09:13:00Z",
        "source_path": f"data/journal/{date.replace('-', '/')}/{entry_id}.md",
        "title": title or "Focus Notes",
        "tags": tags or ["focus", "planning"],
        "sections": [
            {"heading": "Morning Focus", "level": 1},
            {"heading": "Decisions", "level": 2},
        ],
        "source_hash": source_hash,
    }
    _write_yaml(tmp_path / "data" / "normalized" / date / f"{entry_id}.yaml", normalized)


def _seed_manifest(
    tmp_path: Path,
    *,
    entry_id: str = ENTRY_ID,
    date: str = DATE,
    source_hash: str = SOURCE_HASH,
    tags: list[str] | None = None,
) -> None:
    manifest = [
        {
            "hash": source_hash,
            "path": f"data/journal/{date.replace('-', '/')}/{entry_id}.md",
            "normalized": f"data/normalized/{date}/{entry_id}.yaml",
            "source_type": "journal",
            "ingested_at": f"{date}T10:00:00Z",
            "created_at": f"{date}T09:13:00Z",
            "id": entry_id,
            "tags": tags or ["focus"],
            "model": "fake-ollama",
        },
    ]
    manifest_path = tmp_path / "data" / "manifest" / "ingested.yaml"
    if manifest_path.exists():
        existing = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or []
    else:
        existing = []
    existing.extend(manifest)
    _write_yaml(manifest_path, existing)


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


def _summary_path(workspace: Path) -> Path:
    return workspace / "derived" / "summaries" / f"{DATE}.yaml"


def _microfacts_path(workspace: Path) -> Path:
    return workspace / "derived" / "microfacts" / f"{DATE}.yaml"


def _write_summary_artifact(workspace: Path) -> None:
    summary = DailySummary(
        day=DATE,
        bullets=["Protected 8-10am focus block"],
        highlights=["Breakthrough on dashboard"],
        todo_candidates=["Share summary with team"],
    )
    meta = ArtifactMeta(
        created_at=f"{DATE}T12:00:00Z",
        model="fake-ollama",
        prompt_path="tests/synthetic",
    )
    save_artifact(
        _summary_path(workspace),
        Artifact(kind=ArtifactKind.SUMMARY_DAILY, meta=meta, data=summary),
    )


def _write_microfacts_artifact(workspace: Path) -> None:
    fact = MicroFact(
        id="fact-1",
        statement="Protects 8-10am Tue-Thu for deep work",
        confidence=0.82,
        evidence=SourceRef(entry_id=ENTRY_ID, spans=[]),
        first_seen=DATE,
        last_seen=DATE,
    )
    facts = MicroFactsFile(facts=[fact], claim_proposals=[])
    meta = ArtifactMeta(
        created_at=f"{DATE}T12:05:00Z",
        model="fake-ollama",
        prompt_path="tests/synthetic",
    )
    save_artifact(
        _microfacts_path(workspace),
        Artifact(kind=ArtifactKind.MICROFACTS_DAILY, meta=meta, data=facts),
    )


def test_profile_update_cli_writes_batch(cli_workspace: Path, cli_runner: CliRunner) -> None:
    _seed_normalized(cli_workspace)
    _seed_manifest(cli_workspace)
    _seed_profile(cli_workspace)

    result = cli_runner.invoke(app, ["ops", "profile", "update", "--date", DATE])

    assert result.exit_code == 0, result.stdout

    pending_dir = cli_workspace / "derived" / "pending" / "profile_updates"
    batches = sorted(pending_dir.glob("*.yaml"))
    assert batches, "Expected pending batch file"
    artifact = yaml.safe_load(batches[-1].read_text(encoding="utf-8"))
    assert artifact.get("kind") == ArtifactKind.PROFILE_UPDATES.value
    meta = artifact.get("meta", {})
    assert meta.get("prompt_path") == "prompts/profile_update.md"
    data = artifact.get("data", {})
    assert data.get("inputs"), "Profile update batch should include inputs"


def test_profile_update_uses_summary_and_microfacts(
    cli_workspace: Path,
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_normalized(cli_workspace)
    _seed_manifest(cli_workspace)
    _seed_profile(cli_workspace)
    _write_summary_artifact(cli_workspace)
    _write_microfacts_artifact(cli_workspace)

    captured: dict[str, str] = {}

    example_payload = {
        "claims": [
            {
                "type": "habit",
                "statement": "Protects 8-10am Tue-Thu for deep work.",
                "subject": "focus block",
                "predicate": "maintains",
                "value": "Protects 8-10am Tue-Thu",
                "strength": 0.7,
                "status": "tentative",
                "method": "behavioral",
                "reason": "Summary + microfact reinforce recurring block.",
                "evidence_entry": ENTRY_ID,
                "evidence_para": 0,
            },
        ],
        "facets": [],
        "interview_prompts": ["What cancels the 8-10am block?"],
    }

    def fake_invoke(prompt_path: str, variables: dict[str, str], **kwargs) -> PromptProfileUpdates:
        captured.update(variables)
        return PromptProfileUpdates.model_validate(example_payload)

    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "0")
    monkeypatch.setattr(profile_update_module, "invoke_structured_llm", fake_invoke)

    result = cli_runner.invoke(app, ["ops", "profile", "update", "--date", DATE])

    assert result.exit_code == 0, result.stdout
    assert json.loads(captured["summary_json"]) == {
        "day": DATE,
        "bullets": ["Protected 8-10am focus block"],
        "highlights": ["Breakthrough on dashboard"],
        "todo_candidates": ["Share summary with team"],
    }
    microfacts_payload = json.loads(captured["microfacts_json"])
    assert microfacts_payload["facts"][0]["statement"].startswith("Protects 8-10am")


def test_profile_update_sanitizes_batch_filename(
    cli_workspace: Path,
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_normalized(cli_workspace)
    _seed_manifest(cli_workspace)
    _seed_profile(cli_workspace)

    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "0")

    example_payload = {"claims": [], "facets": [], "interview_prompts": []}

    def fake_invoke(*_args, **_kwargs) -> PromptProfileUpdates:
        return PromptProfileUpdates.model_validate(example_payload)

    monkeypatch.setattr(profile_update_module, "invoke_structured_llm", fake_invoke)
    monkeypatch.setattr(
        profile_update_module.time_utils,
        "format_timestamp",
        lambda *_args, **_kwargs: "2025-11-15T10:32:11Z",
    )

    result = cli_runner.invoke(app, ["ops", "profile", "update", "--date", DATE])

    assert result.exit_code == 0, result.stdout

    pending_dir = cli_workspace / "derived" / "pending" / "profile_updates"
    batches = sorted(pending_dir.glob("*.yaml"))
    assert batches, "Profile update should emit a batch"
    filename = batches[-1].name
    assert ":" not in filename
    assert filename == f"{DATE}-2025-11-15T10-32-11Z.yaml"


def test_profile_update_claims_use_entry_manifest_hash(
    cli_workspace: Path,
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    second_entry = f"{DATE}-second"
    second_hash = "secondhash987"

    _seed_normalized(cli_workspace)
    _seed_manifest(cli_workspace)
    _seed_normalized(
        cli_workspace,
        entry_id=second_entry,
        source_hash=second_hash,
        title="Second Entry",
        tags=["focus"],
    )
    _seed_manifest(
        cli_workspace,
        entry_id=second_entry,
        source_hash=second_hash,
        tags=["focus"],
    )
    _seed_profile(cli_workspace)

    example_payload = {
        "claims": [
            {
                "type": "habit",
                "statement": "Reinforces second entry",
                "reason": "LLM cites explicit entry",
                "evidence_entry": second_entry,
                "evidence_para": 0,
            },
        ],
        "facets": [],
        "interview_prompts": [],
    }

    def fake_invoke(*_args, **_kwargs) -> PromptProfileUpdates:
        return PromptProfileUpdates.model_validate(example_payload)

    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "0")
    monkeypatch.setattr(profile_update_module, "invoke_structured_llm", fake_invoke)

    result = cli_runner.invoke(app, ["ops", "profile", "update", "--date", DATE])

    assert result.exit_code == 0, result.stdout

    pending_dir = cli_workspace / "derived" / "pending" / "profile_updates"
    batches = sorted(pending_dir.glob("*.yaml"))
    assert batches, "Profile update should emit a batch"
    artifact = yaml.safe_load(batches[-1].read_text(encoding="utf-8"))
    claim = artifact["data"]["proposals"]["claims"][0]
    assert claim["normalized_ids"] == [second_entry]
    assert claim["manifest_hashes"] == [second_hash]
