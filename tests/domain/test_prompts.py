from __future__ import annotations

from aijournal.domain.changes import ClaimProposal
from aijournal.domain.claims import Scope
from aijournal.domain.enums import ClaimType
from aijournal.domain.prompts import (
    PromptClaimItem,
    PromptProfileUpdates,
    convert_prompt_updates_to_proposals,
)
from aijournal.pipelines.facts import normalize_claim_proposals


def _claim_proposal(**kwargs: object) -> ClaimProposal:
    base = {
        "type": "habit",
        "subject": "focus",
        "predicate": "prefers",
        "value": "Deep work",
        "statement": "Prefers deep work",
        "scope": Scope(),
        "strength": 0.6,
        "status": "tentative",
        "method": "inferred",
        "user_verified": False,
        "review_after_days": 30,
        "normalized_ids": ["scoped-entry"],
        "evidence": [],
        "manifest_hashes": ["scoped-hash"],
        "rationale": "Recent entry shows focus habit",
    }
    base.update(kwargs)
    return ClaimProposal(**base)


def test_convert_prompt_updates_scopes_ids_to_evidence_entry() -> None:
    prompt = PromptProfileUpdates(
        claims=[
            PromptClaimItem(
                type=ClaimType.HABIT,
                statement="Keeps morning focus block",
                evidence_entry="entry-specific",
                reason="Mentioned in entry",
            ),
            PromptClaimItem(
                type=ClaimType.VALUE,
                statement="Values experimentation",
                reason="No specific entry",
            ),
        ],
    )

    result = convert_prompt_updates_to_proposals(
        prompt,
        normalized_ids=["entry-specific", "other-entry"],
        manifest_hashes=["global-hash"],
        entry_hash_lookup={"entry-specific": "scoped-hash"},
    )

    specific, fallback = result.claims
    assert specific.normalized_ids == ["entry-specific"]
    assert specific.manifest_hashes == ["scoped-hash"]
    assert fallback.normalized_ids == ["entry-specific", "other-entry"]
    assert fallback.manifest_hashes == ["global-hash"]


def test_normalize_claim_proposals_keeps_scoped_ids() -> None:
    raw = _claim_proposal().model_dump(mode="python")
    normalized = normalize_claim_proposals(
        raw_claims=[raw],
        normalized_ids=["global-entry"],
        manifest_hashes=["global-hash"],
        default_sources=[],
        timestamp="2025-11-14T10:00:00Z",
    )

    assert normalized[0].normalized_ids == ["scoped-entry"]
    assert normalized[0].manifest_hashes == ["scoped-hash"]


def test_normalize_claim_proposals_falls_back_when_missing_scope() -> None:
    raw = _claim_proposal(normalized_ids=[], manifest_hashes=[]).model_dump(mode="python")
    normalized = normalize_claim_proposals(
        raw_claims=[raw],
        normalized_ids=["global-entry"],
        manifest_hashes=["global-hash"],
        default_sources=[],
        timestamp="2025-11-14T10:00:00Z",
    )

    assert normalized[0].normalized_ids == ["global-entry"]
    assert normalized[0].manifest_hashes == ["global-hash"]
