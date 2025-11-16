from __future__ import annotations

from aijournal.domain.changes import ClaimProposal
from aijournal.domain.claims import Scope
from aijournal.domain.evidence import SourceRef, Span
from aijournal.pipelines import normalization
from aijournal.pipelines.facts import normalize_claim_proposals
from aijournal.services.profile_preview import claim_proposal_to_atom


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
        normalized_ids=["2025-10-26-focus-log"],
        evidence=[evidence],
        manifest_hashes=["focus-log-hash"],
        rationale="Focus reflections captured in daily notes.",
    )


def test_cli_claim_proposal_to_atom_redacts_span_text() -> None:
    proposal = _proposal_with_span_text()
    atom = claim_proposal_to_atom(proposal, timestamp="2025-10-26T07:00:00Z")
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
                    },
                ],
            },
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
