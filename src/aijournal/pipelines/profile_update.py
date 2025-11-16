"""Pipeline helpers for the unified profile update stage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from aijournal.domain.changes import FacetChange, ProfileUpdateProposals
from aijournal.domain.claims import ClaimAtom, ClaimSource
from aijournal.domain.evidence import SourceRef, redact_source_text
from aijournal.fakes import fake_profile_proposals
from aijournal.pipelines import facts as facts_pipeline
from aijournal.pipelines import normalization
from aijournal.utils import time as time_utils
from aijournal.utils.coercion import coerce_float, coerce_int

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aijournal.domain.journal import NormalizedEntry


def normalize_facet_proposals(
    raw_facets: Sequence[Any],
) -> list[FacetChange]:
    proposals: list[FacetChange] = []
    for raw in raw_facets:
        if isinstance(raw, FacetChange):
            proposals.append(raw)
            continue

        payload = raw.model_dump(mode="python") if hasattr(raw, "model_dump") else raw
        if not isinstance(payload, dict):
            continue

        path = payload.get("path") or payload.get("target")
        if not path:
            continue

        evidence_payload = payload.get("evidence") or []
        evidence_sources: list[SourceRef] = []
        for item in evidence_payload:
            try:
                evidence_sources.append(SourceRef.model_validate(item))
            except ValidationError:
                continue

        proposal_data = {
            "path": str(path),
            "value": payload.get("value"),
            "operation": str(payload.get("operation") or "set"),
            "method": payload.get("method"),
            "confidence": coerce_float(payload.get("confidence")),
            "review_after_days": coerce_int(payload.get("review_after_days")),
            "user_verified": payload.get("user_verified"),
            "evidence": evidence_sources,
            "rationale": str(payload.get("rationale") or payload.get("reason") or "").strip()
            or None,
        }

        try:
            proposals.append(FacetChange.model_validate(proposal_data))
        except ValidationError:
            continue
    return proposals


def generate_profile_update(
    entries: Sequence[NormalizedEntry],
    profile: dict[str, Any],
    claims: Sequence[ClaimAtom],
    *,
    use_fake_llm: bool,
    llm_proposals: ProfileUpdateProposals | None,
    context: tuple[list[str], list[str], list[ClaimSource]],
    claim_timestamp: str,
) -> tuple[ProfileUpdateProposals, list[str]]:
    """Produce profile update proposals plus interview prompts for a single day."""
    if use_fake_llm:
        fake = fake_profile_proposals(
            entries,
            profile,
            claims,
            build_claim=_default_fake_claim_builder,
        )
        prompts = list(fake.interview_prompts)
        return fake, prompts

    if llm_proposals is None:
        msg = "llm_proposals must be provided when fake mode is disabled"
        raise ValueError(msg)

    raw_claims = [proposal.model_dump(mode="python") for proposal in llm_proposals.claims]
    raw_facets = [proposal.model_dump(mode="python") for proposal in llm_proposals.facets]
    prompts = [prompt for prompt in llm_proposals.interview_prompts if prompt]

    normalized_ids, manifest_hashes, default_sources = context
    claims_payload = facts_pipeline.normalize_claim_proposals(
        raw_claims,
        normalized_ids=normalized_ids,
        manifest_hashes=manifest_hashes,
        default_sources=default_sources,
        timestamp=claim_timestamp,
    )
    facets_payload = normalize_facet_proposals(raw_facets)
    merged_prompts = facts_pipeline.merge_unique([], prompts)
    proposals = ProfileUpdateProposals(
        claims=claims_payload,
        facets=facets_payload,
        interview_prompts=merged_prompts,
    )
    return proposals, merged_prompts


def _default_fake_claim_builder(
    entry: NormalizedEntry,
    *,
    claim_id: str,
    statement: str,
    strength: float,
    status: str,
) -> ClaimAtom:
    timestamp = time_utils.format_timestamp(time_utils.now())
    default_sources = [ClaimSource(entry_id=entry.id or claim_id, spans=[])]
    sanitized_sources = [
        ClaimSource.model_validate(
            redact_source_text(source).model_dump(mode="python"),
        )
        for source in default_sources
    ]
    raw = {
        "id": claim_id,
        "type": "preference",
        "subject": entry.title or claim_id,
        "predicate": "insight",
        "value": statement,
        "statement": statement,
        "scope": {
            "domain": None,
            "context": list((entry.tags or [])[:2]),
            "conditions": [],
        },
        "strength": strength,
        "status": status,
        "method": "inferred",
        "user_verified": False,
        "review_after_days": 120,
        "provenance": {
            "sources": [source.model_dump(mode="python") for source in sanitized_sources],
            "first_seen": entry.created_at or timestamp,
        },
    }

    return normalization.normalize_claim_atom(
        raw,
        timestamp=timestamp,
        default_sources=sanitized_sources,
    )
