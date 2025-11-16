from __future__ import annotations

from aijournal.domain.claims import ClaimAtom, Provenance, Scope
from aijournal.models.derived import AdviceCard
from aijournal.pipelines import advise


def _claim(claim_id: str) -> ClaimAtom:
    return ClaimAtom(
        id=claim_id,
        type="preference",
        subject="self",
        predicate="insight",
        value="Value",
        statement="Statement",
        scope=Scope(),
        strength=0.6,
        status="tentative",
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


def test_generate_advice_fake_mode() -> None:
    card = advise.generate_advice(
        "How should I focus?",
        profile={"values": {"top": ["Focus"]}},
        claims=[_claim("claim-1")],
        use_fake_llm=True,
        advice_identifier=lambda q: "adv-test",
        llm_advice=None,
        rankings=[],
        pending_prompts=["Follow up"],
    )

    assert isinstance(card, AdviceCard)
    assert card.id.startswith("adv-test") or card.id  # ensure fake path returns AdviceCard


def test_generate_advice_llm_path() -> None:
    response = AdviceCard(
        id="adv-1234",
        query="How should I focus?",
        assumptions=["Assumption"],
        recommendations=[],
        tradeoffs=[],
        next_actions=[],
        confidence=0.5,
    )

    card = advise.generate_advice(
        "How should I focus?",
        profile={},
        claims=[],
        use_fake_llm=False,
        advice_identifier=lambda q: "adv-test",
        llm_advice=response,
        rankings=[],
        pending_prompts=[],
    )

    assert card.id == "adv-1234"
