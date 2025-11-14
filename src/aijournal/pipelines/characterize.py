"""Pipeline helpers for profile characterization."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any, cast

from pydantic import ValidationError

from aijournal.domain.changes import (
    ClaimProposal,
    FacetChange,
    ProfileUpdateProposals,
)
from aijournal.domain.claims import ClaimAtom, ClaimSource
from aijournal.domain.evidence import SourceRef
from aijournal.domain.journal import NormalizedEntry
from aijournal.fakes import fake_characterize
from aijournal.pipelines import facts as facts_pipeline
from aijournal.utils.coercion import coerce_int

StructuredCall = Callable[..., Any]
CharacterizeRequestFactory = Callable[[], ProfileUpdateProposals]
NormalizeClaims = Callable[..., list[ClaimProposal]]
NormalizeFacets = Callable[..., list[FacetChange]]


def normalize_facet_proposals(
    raw_facets: Iterable[Any],
) -> list[FacetChange]:
    proposals: list[FacetChange] = []
    for raw in raw_facets:
        if isinstance(raw, FacetChange):
            proposals.append(raw)
            continue

        payload = raw.model_dump(mode="python") if hasattr(raw, "model_dump") else raw
        if not isinstance(payload, dict):
            continue

        path = payload.get("path")
        if not path:
            continue

        evidence_payload = payload.get("evidence") or []
        evidence_sources = []
        for item in evidence_payload:
            try:
                evidence_sources.append(SourceRef.model_validate(item))
            except ValidationError:
                continue

        proposal_data = {
            "path": str(path),
            "value": payload.get("value"),
            "action": str(payload.get("action") or "set"),
            "evidence": evidence_sources,
            "reason": str(payload.get("reason") or "").strip() or None,
            "evidence_entry": payload.get("evidence_entry"),
            "evidence_para": coerce_int(payload.get("evidence_para")) or 0,
        }

        try:
            proposals.append(FacetChange.model_validate(proposal_data))
        except ValidationError:
            continue
    return proposals


def generate_characterization(
    entries: Sequence[NormalizedEntry],
    profile: dict[str, Any],
    claims: Sequence[ClaimAtom],
    *,
    use_fake_llm: bool,
    structured_call: StructuredCall,
    request_factory: CharacterizeRequestFactory,
    retries: int,
    label: str,
    context: tuple[list[str], list[str], list[ClaimSource]],
    claim_timestamp: str,
    build_claim: Callable[..., ClaimAtom],
    normalize_claims: NormalizeClaims,
    normalize_facets: NormalizeFacets,
) -> tuple[ProfileUpdateProposals, list[str]]:
    """Produce claim/facet proposals along with follow-up prompts."""

    normalized_ids, manifest_hashes, default_sources = context

    if use_fake_llm:
        base = fake_characterize(
            entries,
            profile,
            claims,
            build_claim=build_claim,
        )
        raw_claims = [
            claim.model_dump(mode="python") if hasattr(claim, "model_dump") else claim
            for claim in base.claims
        ]
        raw_facets = [
            facet.model_dump(mode="python") if hasattr(facet, "model_dump") else facet
            for facet in base.facets
        ]
        prompts: list[str] = []
    else:
        response = cast(
            ProfileUpdateProposals,
            structured_call(request_factory, retries=retries, label=label),
        )

        raw_claims = [proposal.model_dump(mode="python") for proposal in response.claims]
        raw_facets = [proposal.model_dump(mode="python") for proposal in response.facets]
        prompts = [prompt for prompt in response.interview_prompts if prompt]

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
