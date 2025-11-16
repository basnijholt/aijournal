"""Tests for the provenance audit command."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from typer.testing import CliRunner

from aijournal.cli import app
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.changes import ClaimAtomInput, ClaimProposal, ProfileUpdateProposals
from aijournal.domain.claims import Scope
from aijournal.domain.evidence import SourceRef, Span
from aijournal.io.artifacts import load_artifact, save_artifact
from aijournal.io.yaml_io import dump_yaml
from aijournal.models.derived import ProfileUpdateBatch
from tests.helpers import make_claim_atom

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_claims_with_text(path: Path) -> None:
    claim = make_claim_atom("pref_focus", "Focus best before lunch")
    claim["provenance"]["sources"][0]["spans"].append(
        {
            "type": "quote",
            "index": 0,
            "start": 0,
            "end": 10,
            "text": "Sensitive",
        },
    )
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
            SourceRef(
                entry_id="2025-01-01_focus",
                spans=[
                    Span(type="quote", index=0, start=0, end=12, text="Another secret"),
                ],
            ),
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
    assert result.exit_code == 1
    assert "profile/claims.yaml" in result.stdout
    assert "derived/pending/profile_updates" in result.stdout
    assert "Run with --fix" in result.stdout

    fix_result = runner.invoke(app, ["ops", "audit", "provenance", "--fix"])
    assert fix_result.exit_code == 0
    assert "Redacted" in fix_result.stdout

    sanitized_claims = yaml.safe_load((workspace / "profile" / "claims.yaml").read_text())
    span_texts = [
        span.get("text")
        for source in sanitized_claims["claims"][0]["provenance"]["sources"]
        for span in source.get("spans", [])
    ]
    assert all(text in (None, "") for text in span_texts)

    batch_artifact = load_artifact(
        workspace / "derived" / "pending" / "profile_updates" / "batch.yaml",
        ProfileUpdateBatch,
    )
    for source in batch_artifact.data.proposals.claims[0].evidence:
        for span in source.spans:
            assert span.text in (None, "")

    second_pass = runner.invoke(app, ["ops", "audit", "provenance"])
    assert second_pass.exit_code == 0
    assert "No provenance span text detected" in second_pass.stdout
