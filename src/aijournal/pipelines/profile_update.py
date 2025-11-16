"""Pipeline helpers for the unified profile update stage."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, cast

from pydantic import ValidationError

from aijournal.domain.changes import ClaimProposal, FacetChange, ProfileUpdateProposals
from aijournal.domain.claims import ClaimAtom, ClaimSource
from aijournal.domain.evidence import SourceRef
from aijournal.domain.journal import NormalizedEntry
from aijournal.fakes import fake_profile_proposals
from aijournal.pipelines import facts as facts_pipeline
from aijournal.utils.coercion import coerce_float, coerce_int

RequestFactory = Callable[[], ProfileUpdateProposals]
NormalizeClaims = Callable[..., list[ClaimProposal]]
NormalizeFacets = Callable[..., list[FacetChange]]
BuildClaim = Callable[..., ClaimAtom]


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
    request_factory: RequestFactory,
    context: tuple[list[str], list[str], list[ClaimSource]],
    claim_timestamp: str,
    build_claim: BuildClaim,
    normalize_claims: NormalizeClaims,
    normalize_facets: NormalizeFacets,
) -> tuple[ProfileUpdateProposals, list[str]]:
    """Produce profile update proposals plus interview prompts for a single day."""

    if use_fake_llm:
        fake = fake_profile_proposals(entries, profile, claims, build_claim=build_claim)
        prompts = list(fake.interview_prompts)
        return fake, prompts

    response = cast(ProfileUpdateProposals, request_factory())

    raw_claims = [proposal.model_dump(mode="python") for proposal in response.claims]
    raw_facets = [proposal.model_dump(mode="python") for proposal in response.facets]
    prompts = [prompt for prompt in response.interview_prompts if prompt]

    normalized_ids, manifest_hashes, default_sources = context
    claims_payload = normalize_claims(
        raw_claims,
        normalized_ids=normalized_ids,
        manifest_hashes=manifest_hashes,
        default_sources=default_sources,
        timestamp=claim_timestamp,
    )
    facets_payload = normalize_facets(raw_facets)
    merged_prompts = facts_pipeline.merge_unique([], prompts)
    proposals = ProfileUpdateProposals(
        claims=claims_payload,
        facets=facets_payload,
        interview_prompts=merged_prompts,
    )
    return proposals, merged_prompts
