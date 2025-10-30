"""Tests for characterize/review pipeline commands."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from aijournal.cli import app
from aijournal.domain.changes import ProfileUpdateProposals

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


def _seed_conflicting_claim(tmp_path: Path) -> None:
    conflict_claim = {
        "id": "focus-notes-conflict",
        "type": "preference",
        "subject": "Focus Notes",
        "predicate": "insight",
        "value": "Afternoon sessions are more effective.",
        "statement": "Afternoon sessions are more effective.",
        "scope": {"domain": None, "context": ["focus", "planning"], "conditions": []},
        "strength": 0.72,
        "status": "accepted",
        "method": "self_report",
        "user_verified": True,
        "review_after_days": 120,
        "provenance": {
            "sources": [{"entry_id": "legacy-entry", "spans": []}],
            "first_seen": f"{DATE}T06:00:00Z",
            "last_updated": f"{DATE}T06:00:00Z",
            "observation_count": 1,
        },
    }
    _write_yaml(tmp_path / "profile" / "claims.yaml", {"claims": [conflict_claim]})


def _run_characterize(
    tmp_path: Path,
    cli_runner: CliRunner,
    extra_args: list[str] | None = None,
    env_override: dict[str, str] | None = None,
) -> tuple[Path, str]:
    args = ["ops", "pipeline", "characterize", "--date", DATE]
    if extra_args:
        args.extend(extra_args)
    result = cli_runner.invoke(app, args, env=env_override)
    assert result.exit_code == 0, result.output
    pending_dir = tmp_path / "derived" / "pending" / "profile_updates"
    batches = sorted(pending_dir.glob("*.yaml"))
    assert batches, "Expected pending batch"
    return batches[-1], result.stdout


def test_characterize_generates_pending_batch(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_normalized(cli_workspace)
    _seed_manifest(cli_workspace)
    _seed_profile(cli_workspace)

    batch_path, _ = _run_characterize(cli_workspace, cli_runner)
    artifact = yaml.safe_load(batch_path.read_text(encoding="utf-8"))
    assert artifact.get("kind") == "profile.updates"
    outer_meta = artifact.get("meta", {})
    assert outer_meta.get("created_at")
    assert outer_meta.get("prompt_path") == "prompts/characterize.md"
    assert outer_meta.get("model") == "fake-ollama"
    data = artifact.get("data", {})

    assert data.get("inputs")
    assert "meta" not in data
    proposals = data.get("proposals", {})
    claims = proposals.get("claims")
    assert claims, "Expected at least one claim proposal"
    first_claim = claims[0]
    assert SOURCE_HASH in (first_claim.get("manifest_hashes") or [])
    evidence = first_claim.get("evidence") or []
    assert any(item.get("entry_id") == ENTRY_ID for item in evidence)
    preview = data.get("preview", {})
    events = preview.get("claim_events") or []
    assert events and events[0].get("action") == "upsert"
    assert not (preview.get("interview_prompts") or [])


def test_review_updates_applies_batch(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_normalized(cli_workspace)
    _seed_manifest(cli_workspace)
    _seed_profile(cli_workspace)

    batch_path, _ = _run_characterize(cli_workspace, cli_runner)
    result = cli_runner.invoke(
        app,
        ["ops", "pipeline", "review", "--file", str(batch_path), "--apply"],
    )
    assert result.exit_code == 0, result.output

    profile_path = cli_workspace / "profile" / "self_profile.yaml"
    claims_path = cli_workspace / "profile" / "claims.yaml"
    profile_data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    claims_data = yaml.safe_load(claims_path.read_text(encoding="utf-8"))

    assert profile_data.get("values_motivations", {}).get("recurring_theme")
    assert claims_data.get("claims"), "Expected claim upsert applied"


def test_characterize_preview_flags_conflict(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_normalized(cli_workspace)
    _seed_manifest(cli_workspace)
    _seed_profile(cli_workspace)
    _seed_conflicting_claim(cli_workspace)

    batch_path, _ = _run_characterize(cli_workspace, cli_runner)
    artifact = yaml.safe_load(batch_path.read_text(encoding="utf-8"))
    data = artifact.get("data", {})
    preview = data.get("preview", {})
    events = preview.get("claim_events") or []
    actions = {event.get("action") for event in events}
    assert "conflict" in actions, "Expected conflict action in preview events"
    prompts = preview.get("interview_prompts") or []
    assert prompts, "Expected interview prompt queued for conflict"


def test_characterize_progress_flag(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_normalized(cli_workspace)
    _seed_manifest(cli_workspace)
    _seed_profile(cli_workspace)

    _, output = _run_characterize(cli_workspace, cli_runner, ["--progress"])

    assert "Characterizing entries" in output
    assert "[1/1]" in output


def test_characterize_live_mode_structured(
    cli_workspace: Path,
    cli_runner: CliRunner,
    monkeypatch,
) -> None:
    _seed_normalized(cli_workspace)
    _seed_manifest(cli_workspace)
    _seed_profile(cli_workspace)
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "0")

    claim_payload = {
        "type": "preference",
        "subject": "Focus routines",
        "predicate": "affinity",
        "value": "Focus routines hold",
        "statement": "Focus routines hold",
        "scope": {"domain": None, "context": ["focus"], "conditions": []},
        "strength": 0.6,
        "status": "tentative",
        "method": "inferred",
        "user_verified": False,
        "review_after_days": 120,
    }

    def _fake_structured(*_args, **_kwargs) -> ProfileUpdateProposals:
        return ProfileUpdateProposals.model_validate(
            {
                "claims": [
                    {
                        "claim": claim_payload,
                        "normalized_ids": [ENTRY_ID],
                        "manifest_hashes": [SOURCE_HASH],
                        "evidence": [{"entry_id": ENTRY_ID, "spans": []}],
                        "rationale": "Recent entry reinforces the pattern.",
                    }
                ],
                "facets": [],
                "interview_prompts": ["How do mornings vary on travel days?"],
            },
        )

    captured: dict[str, list] = {}

    import aijournal.cli as cli_module  # type: ignore[import-deprecated]

    original_normalize = cli_module._normalize_claim_proposals

    def _capture_claims(raw_claims, **kwargs):  # type: ignore[override]
        raw_list = list(raw_claims)
        captured["raw_claims"] = raw_list
        return original_normalize(raw_list, **kwargs)

    monkeypatch.setattr("aijournal.cli._normalize_claim_proposals", _capture_claims)
    monkeypatch.setattr(
        "aijournal.cli._invoke_structured_llm",
        lambda *a, **k: _fake_structured(),
    )

    batch_path, _ = _run_characterize(
        cli_workspace,
        cli_runner,
        env_override={"AIJOURNAL_FAKE_OLLAMA": "0"},
    )
    artifact = yaml.safe_load(batch_path.read_text(encoding="utf-8"))
    data = artifact.get("data", {})
    assert captured.get("raw_claims"), "Expected structured claims to flow into normalization"
    claims = data["proposals"]["claims"]
    assert all("id" not in item.get("claim", {}) for item in claims)
    statements = [item["claim"]["statement"] for item in claims]
    normalized_ids = [item.get("normalized_ids") for item in claims]
    assert any(ENTRY_ID in (ids or []) for ids in normalized_ids)
    assert any("Focus routines hold" in stmt for stmt in statements)
    prompts = data.get("preview", {}).get("interview_prompts") or []
    assert "travel" in prompts[0]
