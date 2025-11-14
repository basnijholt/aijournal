from __future__ import annotations

from pathlib import Path

from aijournal.cli import _claim_proposal_to_atom
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.changes import ClaimProposal, ProfileUpdateProposals
from aijournal.domain.claims import Scope
from aijournal.domain.evidence import SourceRef, Span
from aijournal.io.artifacts import load_artifact_data, save_artifact
from aijournal.pipelines import normalization
from aijournal.pipelines.facts import normalize_claim_proposals


def _proposal_with_span_text() -> ClaimProposal:
    evidence = SourceRef(
        entry_id="2025-10-26-focus-log",
        spans=[Span(type="paragraph", index=0, text="sensitive text")],
    )
    return ClaimProposal(
        type="habit",
        subject="morning routine",
        predicate="reflection",
        value="Reflect after every focus block.",
        statement="Reflect after every focus block.",
        scope=Scope(),
        strength=0.6,
        status="tentative",
        method="inferred",
        user_verified=False,
        review_after_days=45,
        evidence_entry="2025-10-26-focus-log",
        normalized_ids=["2025-10-26-focus-log"],
        evidence=[evidence],
        manifest_hashes=["focus-log-hash"],
        reason="Focus reflections captured in daily notes.",
    )


def test_cli_claim_proposal_to_atom_redacts_span_text() -> None:
    proposal = _proposal_with_span_text()
    atom = _claim_proposal_to_atom(proposal, timestamp="2025-10-26T07:00:00Z")
    for source in atom.provenance.sources:
        for span in source.spans:
            assert span.text is None


def test_normalize_claim_proposals_redacts_span_text() -> None:
    proposal = _proposal_with_span_text()
    raw = proposal.model_dump(mode="python")
    normalized = normalize_claim_proposals(
        raw_claims=[raw],
        normalized_ids=[],
        manifest_hashes=[],
        default_sources=[],
        timestamp="2025-10-26T07:00:00Z",
    )
    assert normalized
    evidence = normalized[0].evidence
    assert evidence
    for source in evidence:
        for span in source.spans:
            assert span.text is None


def test_normalize_provenance_redacts_span_text() -> None:
    raw_provenance = {
        "sources": [
            {
                "entry_id": "2025-10-26-focus-log",
                "spans": [
                    {
                        "type": "paragraph",
                        "index": 0,
                        "text": "still sensitive",
                    }
                ],
            }
        ],
        "last_updated": "2025-10-26T07:00:00Z",
    }
    provenance = normalization.normalize_provenance(
        raw_provenance,
        timestamp="2025-10-26T07:00:00Z",
        default_sources=None,
    )
    for source in provenance.sources:
        for span in source.spans:
            assert span.text is None


def test_claim_proposal_round_trip_preserves_scope_and_ids(tmp_path: Path) -> None:
    proposal = _proposal_with_span_text()
    proposal.scope = Scope(domain="work", context=["weekday"], conditions=["office"])
    proposals = ProfileUpdateProposals(claims=[proposal])
    artifact = Artifact[ProfileUpdateProposals](
        kind=ArtifactKind.PROFILE_PROPOSALS,
        meta=ArtifactMeta(created_at="2025-10-26T07:00:00Z"),
        data=proposals,
    )
    path = tmp_path / "proposal.yaml"
    save_artifact(path, artifact)

    loaded = load_artifact_data(path, ProfileUpdateProposals)
    loaded_claim = loaded.claims[0]

    assert loaded_claim.scope.domain == "work"
    assert loaded_claim.scope.context == ["weekday"]
    assert loaded_claim.scope.conditions == ["office"]
    assert loaded_claim.normalized_ids == ["2025-10-26-focus-log"]
    assert loaded_claim.evidence and loaded_claim.evidence[0].entry_id == "2025-10-26-focus-log"
