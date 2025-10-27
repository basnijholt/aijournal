"""Pipeline helpers for profile characterization."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any, cast

from aijournal.fakes import fake_characterize
from aijournal.models import (
    CharacterizeResponse,
    ClaimAtom,
    ClaimProposal,
    ClaimSource,
    FacetProposal,
    NormalizedEntry,
    ProfileUpdateProposals,
)
from aijournal.pipelines import facts as facts_pipeline
from aijournal.utils.coercion import coerce_float, coerce_int

StructuredCall = Callable[..., Any]
CharacterizeRequestFactory = Callable[[], CharacterizeResponse]
NormalizeClaims = Callable[..., list[ClaimProposal]]
NormalizeFacets = Callable[..., list[FacetProposal]]


def normalize_facet_proposals(
    raw_facets: Iterable[Any],
    *,
    normalized_ids: list[str],
    evidence_hashes: list[str],
) -> list[FacetProposal]:
    proposals: list[FacetProposal] = []
    for raw in raw_facets:
        if isinstance(raw, FacetProposal):
            proposals.append(
                FacetProposal(
                    path=raw.path,
                    value=raw.value,
                    operation=raw.operation,
                    method=raw.method,
                    confidence=raw.confidence,
                    review_after_days=raw.review_after_days,
                    user_verified=raw.user_verified,
                    normalized_ids=facts_pipeline.merge_unique(raw.normalized_ids, normalized_ids),
                    evidence_hashes=facts_pipeline.merge_unique(
                        raw.evidence_hashes, evidence_hashes
                    ),
                    rationale=raw.rationale,
                ),
            )
            continue
        payload = raw.model_dump(mode="python") if hasattr(raw, "model_dump") else raw
        if not isinstance(payload, dict):
            continue
        path = payload.get("path") or payload.get("target")
        if not path:
            continue
        proposals.append(
            FacetProposal(
                path=str(path),
                value=payload.get("value"),
                operation=str(payload.get("operation") or "set"),
                method=str(payload.get("method") or "inferred"),
                confidence=coerce_float(payload.get("confidence")) or 0.55,
                review_after_days=coerce_int(payload.get("review_after_days")) or 90,
                user_verified=bool(payload.get("user_verified", False)),
                normalized_ids=facts_pipeline.merge_unique(
                    payload.get("normalized_ids", []), normalized_ids
                ),
                evidence_hashes=facts_pipeline.merge_unique(
                    payload.get("evidence_hashes", []), evidence_hashes
                ),
                rationale=str(payload.get("rationale") or payload.get("reason") or "").strip()
                or None,
            ),
        )
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
    context: tuple[list[str], list[str], list[str], list[ClaimSource]],
    claim_timestamp: str,
    build_claim: Callable[..., ClaimAtom],
    normalize_claims: NormalizeClaims,
    normalize_facets: NormalizeFacets,
) -> tuple[ProfileUpdateProposals, list[str]]:
    """Produce claim/facet proposals along with follow-up prompts."""

    normalized_ids, evidence_hashes, manifest_hashes, default_sources = context

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
            CharacterizeResponse,
            structured_call(request_factory, retries=retries, label=label),
        )

        raw_claims = [proposal.model_dump(mode="python") for proposal in response.claims]
        raw_facets = [proposal.model_dump(mode="python") for proposal in response.facets]
        prompts = [prompt for prompt in response.interview_prompts if prompt]

    claims_payload = normalize_claims(
        raw_claims,
        normalized_ids=normalized_ids,
        evidence_hashes=evidence_hashes,
        manifest_hashes=manifest_hashes,
        default_sources=default_sources,
        timestamp=claim_timestamp,
    )
    facets_payload = normalize_facets(
        raw_facets,
        normalized_ids=normalized_ids,
        evidence_hashes=evidence_hashes,
    )
    return ProfileUpdateProposals(claims=claims_payload, facets=facets_payload), prompts


__all__ = [
    "generate_characterization",
    "normalize_facet_proposals",
]
