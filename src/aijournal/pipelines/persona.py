"""Pipeline helpers for building persona cores from profile data."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from math import ceil, exp
from typing import TYPE_CHECKING, Any

from aijournal.domain.persona import PersonaCore
from aijournal.io.yaml_io import dump_yaml
from aijournal.utils.coercion import coerce_float

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aijournal.domain.claims import ClaimAtom

PERSONA_PROFILE_KEYS = (
    "values_motivations",
    "goals",
    "boundaries_ethics",
    "coaching_prefs",
    "affect_energy",
    "decision_style",
    "traits",
    "social",
)

CLAIM_TYPE_IMPACT_DEFAULTS: dict[str, float] = {
    "value": 1.4,
    "goal": 1.4,
    "boundary": 1.3,
    "trait": 1.2,
    "preference": 1.0,
    "habit": 0.9,
    "aversion": 1.1,
    "skill": 1.0,
}


@dataclass
class PersonaClaimSelection:
    """Persona claim selection result with token accounting."""

    claims: list[ClaimAtom]
    trimmed_ids: list[str]
    planned_tokens: int
    budget_exceeded: bool


@dataclass
class PersonaBuildResult:
    """Aggregated persona output including metadata ingredients."""

    persona: PersonaCore
    selection: PersonaClaimSelection
    ranked_claims: list[ClaimAtom]
    profile_slice: dict[str, Any]


def _persona_profile_slice(profile: dict[str, Any]) -> dict[str, Any]:
    if not profile:
        return {}
    subset: dict[str, Any] = {}
    for key in PERSONA_PROFILE_KEYS:
        value = profile.get(key)
        if value is None:
            continue
        subset[key] = copy.deepcopy(value)
    if not subset:
        return copy.deepcopy(profile)
    return subset


def _claim_weight(claim: ClaimAtom, weights: dict[str, Any]) -> float:
    claim_type = str(claim.type or "preference")
    claim_types_raw = weights.get("claim_types")
    claim_weights = claim_types_raw if isinstance(claim_types_raw, dict) else {}
    if claim_type in claim_weights:
        return coerce_float(claim_weights[claim_type]) or 1.0
    if "default" in claim_weights:
        return coerce_float(claim_weights["default"]) or 1.0
    if "claims" in weights:
        return coerce_float(weights["claims"]) or 1.0
    return CLAIM_TYPE_IMPACT_DEFAULTS.get(claim_type, 1.0)


def _claim_effective_strength(
    claim: ClaimAtom,
    *,
    weights: dict[str, Any],
    now: datetime,
) -> float:
    strength = max(0.0, min(1.0, float(claim.strength or 0.5)))
    weight = _claim_weight(claim, weights)
    last_updated = str(claim.provenance.last_updated or "")
    try:
        dt = datetime.fromisoformat(last_updated)
    except ValueError:
        dt = now
    days_since = (now - dt).days if now >= dt else 0
    review_after = int(claim.review_after_days or 120)
    staleness = min(2.0, max(0.0, days_since / max(review_after, 1)))
    decay = exp(-0.2 * staleness)
    status = str(claim.status or "tentative").lower()
    status_bonus = 0.05 if status == "accepted" else 0.0
    return (strength * weight * decay) + status_bonus


def _rank_claims_for_persona(
    claims: Sequence[ClaimAtom],
    weights: dict[str, Any],
    now: datetime,
) -> list[ClaimAtom]:
    if not claims:
        return []
    ranked: list[tuple[int, float, ClaimAtom]] = []
    status_priority = {"accepted": 0, "tentative": 1, "rejected": 2}
    for claim in claims:
        score = _claim_effective_strength(claim, weights=weights, now=now)
        status = str(claim.status or "tentative").lower()
        priority = status_priority.get(status, 1)
        ranked.append((priority, -score, claim))
    ranked.sort(key=lambda item: (item[0], item[1], item[2].id))
    return [entry[2].model_copy(deep=True) for entry in ranked]


def _estimate_persona_tokens(persona_block: dict[str, Any], char_per_token: float) -> int:
    width = max(char_per_token, 0.01)
    text = dump_yaml(persona_block, sort_keys=False)
    return max(1, ceil(len(text) / width))


def _select_persona_claims(
    claims: Sequence[ClaimAtom],
    profile_slice: dict[str, Any],
    *,
    token_budget: int,
    char_per_token: float,
    min_claims: int,
    max_claims: int,
) -> PersonaClaimSelection:
    selected = [claim.model_copy(deep=True) for claim in claims[:max_claims]]

    def persona_block() -> dict[str, Any]:
        return {
            "profile": profile_slice,
            "claims": [claim.model_dump(mode="python") for claim in selected],
        }

    tokens = _estimate_persona_tokens(persona_block(), char_per_token)
    trimmed_ids: list[str] = []
    while tokens > token_budget and len(selected) > min_claims and selected:
        removed = selected.pop()
        trimmed_ids.append(removed.id)
        tokens = _estimate_persona_tokens(persona_block(), char_per_token)
    budget_exceeded = tokens > token_budget
    return PersonaClaimSelection(
        claims=selected,
        trimmed_ids=trimmed_ids,
        planned_tokens=tokens,
        budget_exceeded=budget_exceeded,
    )


def build_persona_core(
    profile: dict[str, Any],
    claims: Sequence[ClaimAtom],
    *,
    token_budget: int,
    max_claims: int,
    min_claims: int,
    char_per_token: float,
    impact_weights: dict[str, Any],
    now: datetime,
) -> PersonaBuildResult:
    """Construct a persona core and selection metadata."""
    profile_slice = _persona_profile_slice(profile)
    ranked_claims = _rank_claims_for_persona(claims, impact_weights, now)

    if not profile_slice and not ranked_claims:
        msg = "Nothing to include in persona core; add profile facets or claims first."
        raise ValueError(msg)

    selection = _select_persona_claims(
        ranked_claims,
        profile_slice,
        token_budget=token_budget,
        char_per_token=char_per_token,
        min_claims=min_claims,
        max_claims=max_claims,
    )

    persona = PersonaCore(
        profile=copy.deepcopy(profile_slice),
        claims=[claim.model_copy(deep=True) for claim in selection.claims],
    )

    return PersonaBuildResult(
        persona=persona,
        selection=selection,
        ranked_claims=[claim.model_copy(deep=True) for claim in ranked_claims],
        profile_slice=profile_slice,
    )
