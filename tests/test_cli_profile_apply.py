"""Tests for `aijournal profile apply` using fake LLM mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.changes import (
    ClaimAtomInput,
    ClaimProposal,
    FacetChange,
    ProfileUpdateProposals,
)
from aijournal.domain.claims import ClaimAtom
from aijournal.domain.evidence import SourceRef
from aijournal.io.artifacts import save_artifact
from aijournal.io.yaml_io import dump_yaml
from aijournal.models.derived import ProfileUpdateBatch, ProfileUpdateInput, ProfileUpdatePreview
from tests.helpers import make_claim_atom

if TYPE_CHECKING:
    from pathlib import Path

DATE = "2025-02-03"


def _has_profile_apply() -> bool:
    result = CliRunner().invoke(app, ["ops", "profile", "apply", "--help"])
    return result.exit_code == 0


@pytest.fixture(autouse=True)
def skip_if_missing() -> None:
    if not _has_profile_apply():
        pytest.skip("profile apply command not available yet")


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(payload, sort_keys=False), encoding="utf-8")


def _seed_authoritative(workspace: Path) -> None:
    self_profile = {
        "values_motivations": {
            "schwartz_top5": ["Universalism"],
            "last_updated": f"{DATE}T09:00:00Z",
            "review_after_days": 90,
        },
    }
    claims = {
        "claims": [
            make_claim_atom(
                "pref_focus",
                "Focus best before lunch",
                strength=0.82,
                status="accepted",
                last_updated=f"{DATE}T09:00:00Z",
            ),
        ],
    }
    _write_yaml(workspace / "profile" / "self_profile.yaml", self_profile)
    _write_yaml(workspace / "profile" / "claims.yaml", claims)


def _seed_suggestions(workspace: Path) -> Path:
    proposed_claim = ClaimAtom.model_validate(
        make_claim_atom(
            "pref_evening",
            "Prefers evening walks",
            strength=0.6,
            status="tentative",
            method="inferred",
            last_updated=f"{DATE}T10:00:00Z",
        ),
    )
    claim_input = ClaimAtomInput(
        type=proposed_claim.type,
        subject=proposed_claim.subject,
        predicate=proposed_claim.predicate,
        value=proposed_claim.value,
        statement=proposed_claim.statement,
        scope=proposed_claim.scope,
        strength=proposed_claim.strength,
        status=proposed_claim.status,
        method=proposed_claim.method,
        user_verified=proposed_claim.user_verified,
        review_after_days=proposed_claim.review_after_days,
    )
    claim_proposal = ClaimProposal(
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
        normalized_ids=[proposed_claim.id],
        evidence=[SourceRef(entry_id="2025-02-03_pref_evening", spans=[])],
        rationale="Detected new evening preference",
    )
    facet_change = FacetChange(
        path="values_motivations.schwartz_top5",
        operation="set",
        value=["Universalism", "Benevolence"],
        evidence=[SourceRef(entry_id="profile.snapshot", spans=[])],
    )
    proposals = ProfileUpdateProposals(
        claims=[claim_proposal],
        facets=[facet_change],
    )
    batch = ProfileUpdateBatch(
        batch_id=f"{DATE}-batch",
        created_at=f"{DATE}T10:00:00Z",
        date=DATE,
        inputs=[
            ProfileUpdateInput(
                id=f"{DATE}-entry",
                normalized_path=f"data/normalized/{DATE}/entry.yaml",
                tags=["evening"],
            ),
        ],
        proposals=proposals,
        preview=ProfileUpdatePreview(),
    )
    artifact = Artifact[ProfileUpdateBatch](
        kind=ArtifactKind.PROFILE_UPDATES,
        meta=ArtifactMeta(
            created_at=f"{DATE}T10:00:00Z",
            model="fake-ollama",
            prompt_path="prompts/profile_update.md",
            prompt_hash="seed",
        ),
        data=batch,
    )
    path = workspace / "derived" / "pending" / "profile_updates" / f"{batch.batch_id}.yaml"
    save_artifact(path, artifact)
    return path


def _invoke(suggestions_path: Path, cli_runner: CliRunner) -> str:
    args = [
        "ops",
        "profile",
        "apply",
        "--date",
        DATE,
        "--file",
        str(suggestions_path),
        "--yes",
    ]
    result = cli_runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result.output


def test_profile_apply_merges_suggestions(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_authoritative(cli_workspace)
    suggestions_path = _seed_suggestions(cli_workspace)

    output = _invoke(suggestions_path, cli_runner)
    assert "Applied" in output

    claims = yaml.safe_load((cli_workspace / "profile" / "claims.yaml").read_text(encoding="utf-8"))
    statements = {claim["statement"] for claim in claims["claims"]}
    assert any("evening" in stmt.lower() for stmt in statements)
    assert len(claims["claims"]) == len(statements), "Duplicate claim statements"

    profile = yaml.safe_load(
        (cli_workspace / "profile" / "self_profile.yaml").read_text(encoding="utf-8"),
    )
    assert {
        "Universalism",
        "Benevolence",
    } == set(profile["values_motivations"]["schwartz_top5"])


def test_profile_apply_idempotent(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_authoritative(cli_workspace)
    suggestions_path = _seed_suggestions(cli_workspace)

    first_output = _invoke(suggestions_path, cli_runner)
    claims_after_first = (cli_workspace / "profile" / "claims.yaml").read_text(encoding="utf-8")
    profile_after_first = (cli_workspace / "profile" / "self_profile.yaml").read_text(
        encoding="utf-8",
    )

    second_output = _invoke(suggestions_path, cli_runner)

    assert (cli_workspace / "profile" / "claims.yaml").read_text(
        encoding="utf-8",
    ) == claims_after_first
    assert (cli_workspace / "profile" / "self_profile.yaml").read_text(
        encoding="utf-8",
    ) == profile_after_first
    assert "Applied" in first_output
    assert "No changes" in second_output or second_output == first_output
