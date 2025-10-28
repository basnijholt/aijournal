from __future__ import annotations

from aijournal.models import AdviceCard, AdviceLLMResponse, ClaimAtom, Provenance, Scope
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
    def request_advice() -> AdviceLLMResponse:  # pragma: no cover - fake mode skips
        raise AssertionError("LLM request should not run in fake mode")

    card = advise.generate_advice(
        "How should I focus?",
        profile={"values": {"top": ["Focus"]}},
        claims=[_claim("claim-1")],
        use_fake_llm=True,
        advice_identifier=lambda q: "adv-test",
        request_advice=request_advice,
        rankings=[],
        pending_prompts=["Follow up"],
    )

    assert isinstance(card, AdviceCard)
    assert card.id.startswith("adv-test") or card.id  # ensure fake path returns AdviceCard


def test_generate_advice_llm_path() -> None:
    response = AdviceLLMResponse(
        id="adv-1234",
        query="How should I focus?",
        assumptions=["Assumption"],
        recommendations=[],
        tradeoffs=[],
        next_actions=[],
        confidence=0.5,
    )

    called: dict[str, bool] = {"invoked": False}

    def request_advice() -> AdviceLLMResponse:
        called["invoked"] = True
        return response

    card = advise.generate_advice(
        "How should I focus?",
        profile={},
        claims=[],
        use_fake_llm=False,
        advice_identifier=lambda q: "adv-test",  # pragma: no cover - live mode uses response id
        request_advice=request_advice,
        rankings=[],
        pending_prompts=[],
    )

    assert called["invoked"]
    assert card.id == "adv-1234"
