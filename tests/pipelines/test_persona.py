from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aijournal.domain.claims import ClaimAtom, Provenance, Scope
from aijournal.pipelines import persona as persona_pipeline


def _test_claim(claim_id: str, *, status: str = "accepted") -> ClaimAtom:
    return ClaimAtom(
        id=claim_id,
        type="preference",
        subject="Self",
        predicate="insight",
        value=f"{claim_id} value",
        statement=f"{claim_id} statement",
        scope=Scope(),
        strength=0.8,
        status=status,
        method="inferred",
        user_verified=False,
        review_after_days=120,
        provenance=Provenance(
            sources=[],
            first_seen="2024-01-01",
            last_updated="2024-01-02T00:00:00Z",
            observation_count=1,
        ),
    )


def test_build_persona_core_requires_content() -> None:
    with pytest.raises(ValueError, match="Nothing to include in persona core"):
        persona_pipeline.build_persona_core(
            {},
            [],
            token_budget=100,
            max_claims=5,
            min_claims=1,
            char_per_token=4.0,
            impact_weights={},
            now=datetime(2024, 1, 2, tzinfo=UTC),
        )


def test_build_persona_core_trims_to_budget() -> None:
    profile = {"traits": {"strengths": ["Focused work"]}}
    claims = [_test_claim("claim-1", status="accepted"), _test_claim("claim-2", status="tentative")]

    result = persona_pipeline.build_persona_core(
        profile,
        claims,
        token_budget=1,
        max_claims=2,
        min_claims=1,
        char_per_token=1.0,
        impact_weights={},
        now=datetime(2024, 1, 2, tzinfo=UTC),
    )

    assert len(result.ranked_claims) == 2
    assert result.selection.trimmed_ids, "Expected trimming when budget is tight"
    assert len(result.persona.claims) == 1
    assert result.persona.profile == profile
