"""Pipeline helpers for generating advice cards."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aijournal.fakes import fake_advise

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from aijournal.domain.claims import ClaimAtom
    from aijournal.models.derived import AdviceCard


def generate_advice(
    question: str,
    profile: dict[str, Any],
    claims: Sequence[ClaimAtom],
    *,
    use_fake_llm: bool,
    advice_identifier: Callable[[str], str],
    llm_advice: AdviceCard | None,
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

    if llm_advice is None:
        msg = "llm_advice must be provided when fake mode is disabled"
        raise ValueError(msg)
    advice = llm_advice.model_copy(deep=True)
    if not advice.id:
        advice.id = advice_identifier(question)
    return advice
