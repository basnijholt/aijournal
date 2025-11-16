"""Tests for the provenance audit command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from aijournal.cli import app
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.changes import ClaimAtomInput, ClaimProposal, ProfileUpdateProposals
from aijournal.domain.claims import Scope
from aijournal.domain.evidence import SourceRef
from aijournal.io.artifacts import save_artifact
from aijournal.io.yaml_io import dump_yaml
from aijournal.models.derived import ProfileUpdateBatch
from tests.helpers import make_claim_atom

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_claims_with_text(path: Path) -> None:
    claim = make_claim_atom("pref_focus", "Focus best before lunch")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml({"claims": [claim]}, sort_keys=False), encoding="utf-8")


def _write_profile_update_batch(path: Path) -> None:
    scope = Scope()
    claim_input = ClaimAtomInput(
        type="preference",
        subject="self",
        predicate="focus",
        value="value",
        statement="Stay focused",
        scope=scope,
        strength=0.5,
        status="tentative",
        method="inferred",
        user_verified=False,
        review_after_days=30,
    )
    proposal = ClaimProposal(
        type=claim_input.type,
        subject=claim_input.subject,
        predicate=claim_input.predicate,
        value=claim_input.value,
        statement=claim_input.statement,
        scope=claim_input.scope,
        strength=claim_input.strength,
        status=claim_input.status,
        method=claim_input.method,
        user_verified=claim_input.user_verified,
        review_after_days=claim_input.review_after_days,
        normalized_ids=["2025-01-01_focus"],
        evidence=[
            SourceRef(entry_id="2025-01-01_focus"),
        ],
        manifest_hashes=["hash-123"],
    )
    batch = ProfileUpdateBatch(
        batch_id="batch-1",
        created_at="2025-01-01T00:00:00Z",
        date="2025-01-01",
        proposals=ProfileUpdateProposals(claims=[proposal]),
    )
    artifact = Artifact[ProfileUpdateBatch](
        kind=ArtifactKind.PROFILE_UPDATES,
        meta=ArtifactMeta(created_at="2025-01-01T00:00:00Z"),
        data=batch,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    save_artifact(path, artifact)


def test_audit_provenance_reports_and_fixes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path
    _write_claims_with_text(workspace / "profile" / "claims.yaml")
    _write_profile_update_batch(
        workspace / "derived" / "pending" / "profile_updates" / "batch.yaml",
    )

    runner = CliRunner()
    monkeypatch.chdir(workspace)

    result = runner.invoke(app, ["ops", "audit", "provenance"])
    assert result.exit_code == 0
    assert "No provenance span text detected" in result.stdout

    fix_result = runner.invoke(app, ["ops", "audit", "provenance", "--fix"])
    assert fix_result.exit_code == 0
    assert "No provenance span text detected" in fix_result.stdout
