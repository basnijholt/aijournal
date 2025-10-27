"""Tests for translating simplified suggestions into full profile suggestions."""

from __future__ import annotations

from aijournal.commands.profile import _simple_suggestions_to_profile
from aijournal.models import (
    ProfileSuggestionUpdate,
    SimpleProfileSuggestionsResponse,
    SimpleSuggestion,
)


def test_simple_suggestions_to_profile_converts_claim_and_facet() -> None:
    response = SimpleProfileSuggestionsResponse(
        suggestions=[
            SimpleSuggestion(
                kind="claim",
                statement="Evening walks help you decompress.",
                evidence=["2024-12-04"],
                status="accepted",
                confidence=0.8,
                rationale="Grounded in the 2024-12-04 journal entry.",
            ),
            SimpleSuggestion(
                kind="facet",
                facet_path="coaching_prefs.check_ins.cadence",
                value="weekly",
                evidence=["2024-12-05"],
                rationale="Mentioned during the weekly review.",
            ),
        ],
    )

    profile_suggestions = _simple_suggestions_to_profile(response, timestamp="2025-01-01T00:00:00Z")

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


def test_simple_suggestions_skip_incomplete_entries() -> None:
    response = SimpleProfileSuggestionsResponse(
        suggestions=[
            SimpleSuggestion(kind="claim"),  # missing statement
            SimpleSuggestion(kind="facet", facet_path="", value=None),
        ],
    )

    profile_suggestions = _simple_suggestions_to_profile(response, timestamp="2025-01-01T00:00:00Z")
    assert not profile_suggestions.upserts
    assert not profile_suggestions.updates
