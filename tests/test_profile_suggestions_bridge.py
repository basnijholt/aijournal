"""Tests for translating simplified suggestions into full profile suggestions."""

from __future__ import annotations

from aijournal.commands.profile import _proposals_to_profile
from aijournal.domain.changes import (
    ClaimAtomInput,
    ClaimProposal,
    FacetChange,
    ProfileUpdateProposals,
)
from aijournal.domain.evidence import SourceRef
from aijournal.models.derived import ProfileSuggestions, ProfileSuggestionUpdate
from aijournal.utils import time as time_utils


def test_proposals_to_profile_converts_claim_and_facet() -> None:
    proposals = ProfileUpdateProposals(
        claims=[
            ClaimProposal(
                claim=ClaimAtomInput(
                    type="preference",
                    subject="self",
                    predicate="insight",
                    value="Evening walks help you decompress.",
                    statement="Evening walks help you decompress.",
                    scope={},
                    strength=0.8,
                    status="accepted",
                    method="inferred",
                    user_verified=False,
                    review_after_days=90,
                ),
                normalized_ids=["2024-12-04"],
                evidence=[SourceRef(entry_id="2024-12-04", spans=[])],
                rationale="Grounded in the 2024-12-04 journal entry.",
            )
        ],
        facets=[
            FacetChange(
                path="coaching_prefs.check_ins.cadence",
                operation="set",
                value="weekly",
                evidence=[SourceRef(entry_id="2024-12-05", spans=[])],
                rationale="Mentioned during the weekly review.",
            )
        ],
    )

    timestamp = time_utils.format_timestamp(time_utils.now())
    profile_suggestions = _proposals_to_profile(proposals, timestamp=timestamp)

    assert isinstance(profile_suggestions, ProfileSuggestions)

    assert profile_suggestions.upserts, "expected claim suggestion"
    claim_upsert = profile_suggestions.upserts[0]
    claim = claim_upsert.value
    assert claim.statement == "Evening walks help you decompress."
    assert claim.status == "accepted"
    assert abs(claim.strength - 0.8) < 1e-6
    assert claim.method == "inferred"
    assert claim.provenance.sources[0].entry_id == "2024-12-04"

    assert profile_suggestions.updates, "expected facet suggestion"
    facet_update = profile_suggestions.updates[0]
    assert isinstance(facet_update, ProfileSuggestionUpdate)
    assert facet_update.target == "coaching_prefs.check_ins.cadence"
    assert facet_update.value == "weekly"
    assert facet_update.evidence == ["2024-12-05"]


def test_proposals_to_profile_skips_incomplete_claims() -> None:
    proposals = ProfileUpdateProposals(
        claims=[
            ClaimProposal(
                claim=ClaimAtomInput(
                    type="preference",
                    subject="self",
                    predicate="insight",
                    value="",
                    statement="",
                    scope={},
                    strength=0.5,
                    status="tentative",
                    method="inferred",
                    user_verified=False,
                    review_after_days=30,
                ),
            )
        ],
        facets=[],
    )

    profile_suggestions = _proposals_to_profile(proposals, timestamp="2025-01-01T00:00:00Z")
    assert not profile_suggestions.upserts
    assert not profile_suggestions.updates
