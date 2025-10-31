from __future__ import annotations

import math
from copy import deepcopy

import pytest

from aijournal.services.consolidator import ClaimConsolidator
from tests.helpers import make_claim_atom


def test_claim_consolidator_merges_strength_and_observation_count() -> None:
    existing_claim = make_claim_atom(
        "preference.deep-work.window",
        "Prefers deep work in the morning",
        strength=0.5,
        status="accepted",
        last_updated="2025-10-20T08:00:00Z",
    )
    incoming_claim = make_claim_atom(
        "preference.deep-work.window",
        "Prefers deep work in the morning",
        strength=0.9,
        status="accepted",
        last_updated="2025-10-25T08:00:00Z",
    )
    claims = [deepcopy(existing_claim)]
    consolidator = ClaimConsolidator(timestamp="2025-10-26T03:00:00Z")

    outcome = consolidator.upsert(claims, incoming_claim)

    assert outcome.changed is True
    assert outcome.action == "strength_delta"
    assert outcome.conflict is None
    updated = claims[0]
    w_prev = min(1.0, math.log1p(1))
    merged_strength = (w_prev * 0.5 + 1.0 * 0.9) / (w_prev + 1.0)
    assert math.isclose(updated["strength"], merged_strength, rel_tol=1e-5)
    assert math.isclose(outcome.delta_strength, merged_strength - 0.5, rel_tol=1e-5)
    assert updated["provenance"]["observation_count"] == 2
    assert updated["provenance"]["last_updated"] == "2025-10-26T03:00:00Z"


def test_claim_consolidator_splits_scope_on_weekend_conflict() -> None:
    existing_claim = make_claim_atom(
        "preference.deep-work.window",
        "Prefers deep work in the morning on weekdays.",
        value="Morning focus weekdays",
        strength=0.82,
        status="accepted",
        last_updated="2025-10-20T08:00:00Z",
    )
    incoming_claim = make_claim_atom(
        "preference.deep-work.window",
        "Prefers later focus blocks on weekends.",
        value="Afternoon focus weekends",
        strength=0.7,
        status="accepted",
        last_updated="2025-10-25T08:00:00Z",
    )
    claims = [deepcopy(existing_claim)]
    consolidator = ClaimConsolidator(timestamp="2025-10-26T03:00:00Z")

    outcome = consolidator.upsert(claims, incoming_claim)

    assert outcome.action == "conflict"
    assert outcome.changed is True
    assert outcome.related_claim_id
    assert outcome.conflict is not None
    assert outcome.related_action in {"upsert", "update", "strength_delta"}
    assert len(claims) == 2

    weekday_claim = claims[0]
    assert "weekday" in [item.lower() for item in weekday_claim["scope"]["context"]]
    assert weekday_claim["status"] == "accepted"
    weekend_claim = next(claim for claim in claims if claim["id"] == outcome.related_claim_id)
    assert "weekend" in [item.lower() for item in weekend_claim["scope"]["context"]]
    assert weekend_claim["value"] == "Afternoon focus weekends"
    assert weekend_claim["status"] == "accepted"


def test_claim_consolidator_flags_conflicts_and_downgrades_strength() -> None:
    existing_claim = make_claim_atom(
        "preference.deep-work.window",
        "Prefers deep work in the morning",
        value="Morning focus",
        strength=0.8,
        status="accepted",
        last_updated="2025-10-20T08:00:00Z",
    )
    incoming_claim = make_claim_atom(
        "preference.deep-work.window",
        "Prefers deep work in the morning",
        value="Evening focus",
        strength=0.7,
        status="accepted",
        last_updated="2025-10-25T08:00:00Z",
    )
    claims = [deepcopy(existing_claim)]
    consolidator = ClaimConsolidator(timestamp="2025-10-26T03:00:00Z")

    outcome = consolidator.upsert(claims, incoming_claim)

    assert outcome.changed is True
    assert outcome.action == "conflict"
    assert outcome.delta_strength == pytest.approx(-0.15, abs=1e-6)
    assert outcome.conflict is not None
    conflict = outcome.conflict
    assert conflict.existing_value == "Morning focus"
    assert conflict.incoming_value == "Evening focus"
    updated = claims[0]
    assert updated["status"] == "tentative"
    assert updated["strength"] == pytest.approx(0.65, abs=1e-6)
    assert updated["provenance"]["observation_count"] == 2
    assert updated["provenance"]["last_updated"] == "2025-10-26T03:00:00Z"
