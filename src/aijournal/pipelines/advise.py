"""Pipeline helpers for generating advice cards."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from aijournal.fakes import fake_advise
from aijournal.models.claim_atoms import ClaimAtom
from aijournal.models.derived import AdviceCard
from aijournal.models.responses import AdviceLLMResponse

AdviceRequest = Callable[[], AdviceLLMResponse]
AdviceIdentifier = Callable[[str], str]


def generate_advice(
    question: str,
    profile: dict[str, Any],
    claims: Sequence[ClaimAtom],
    *,
    use_fake_llm: bool,
    advice_identifier: AdviceIdentifier,
    request_advice: AdviceRequest,
    rankings: Sequence[object],
    pending_prompts: Sequence[str],
) -> AdviceCard:
    """Produce an `AdviceCard` for the given question."""

    if use_fake_llm:
        return fake_advise(
            question,
            profile,
            claims,
            advice_identifier=advice_identifier,
            rankings=rankings,
            pending_prompts=pending_prompts,
        )

    response = request_advice()
    return AdviceCard.model_validate(response.model_dump(mode="python"))
