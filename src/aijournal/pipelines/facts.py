"""Pipeline helpers for generating micro-facts and claim proposals."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any, cast

from pydantic import ValidationError

from aijournal.fakes import fake_microfacts
from aijournal.models import (
    ClaimAtom,
    ClaimProposal,
    ClaimProposalPayload,
    ClaimSketch,
    ClaimSource,
    ClaimSourceSpan,
    ExtractedFactsResponse,
    ManifestEntry,
    MicroFact,
    MicroFactsFile,
    NormalizedEntry,
    Scope,
)
from aijournal.pipelines import normalization
from aijournal.utils import time as time_utils

StructuredCall = Callable[..., Any]
FactsRequestFactory = Callable[[], ExtractedFactsResponse]


def merge_unique(existing: Iterable[str], extras: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for value in existing:
        if not value:
            continue
        key = str(value)
        if key in seen:
            continue
        seen.add(key)
        merged.append(key)
    for value in extras:
        if not value:
            continue
        key = str(value)
        if key in seen:
            continue
        seen.add(key)
        merged.append(key)
    return merged


def _fact_sources_from_evidence(fact: MicroFact) -> list[ClaimSource]:
    evidence = fact.evidence
    if evidence is None:
        return []
    spans: list[ClaimSourceSpan] = []
    for span in evidence.spans or []:
        spans.append(
            ClaimSourceSpan(
                type=span.type,
                index=span.index,
                start=span.start,
                end=span.end,
            )
        )
    if not evidence.entry_id:
        return []
    return [ClaimSource(entry_id=evidence.entry_id, spans=spans)]


def _scope_from_fact(
    fact: MicroFact,
    entry: NormalizedEntry | None,
) -> Scope:
    domain = entry.source_type if entry and entry.source_type else None
    context_candidates: list[str] = []
    if entry and entry.tags:
        context_candidates.extend(tag for tag in entry.tags if tag)

    statement_lower = fact.statement.lower()
    keyword_pairs = {
        "weekday": ("weekday", "weekdays", "workday", "workdays"),
        "weekend": ("weekend", "weekends"),
        "solo": ("solo", "independent", "alone"),
        "team": ("team", "collaborative", "pairing", "group"),
    }
    for label, keywords in keyword_pairs.items():
        if any(word in statement_lower for word in keywords):
            context_candidates.append(label)

    unique_context = merge_unique(context_candidates, [])
    return Scope(
        domain=domain,
        context=unique_context,
        conditions=[],
    )


def _microfact_claim_proposals(
    facts: Sequence[MicroFact],
    *,
    entries: Sequence[NormalizedEntry],
    manifest_index: dict[str, ManifestEntry],
    timestamp: str,
) -> list[ClaimProposal]:
    entry_by_id: dict[str, NormalizedEntry] = {}
    for entry_model in entries:
        if entry_model.id:
            entry_by_id[entry_model.id] = entry_model

    proposals: list[ClaimProposal] = []
    for fact in facts:
        if not fact.statement.strip():
            continue
        evidence_sources = _fact_sources_from_evidence(fact)
        entry_id = fact.evidence.entry_id if fact.evidence else None
        entry: NormalizedEntry | None = entry_by_id.get(entry_id) if entry_id else None
        scope = _scope_from_fact(fact, entry)

        provenance_sources = (
            evidence_sources
            if evidence_sources
            else (
                [ClaimSource(entry_id=entry_id, spans=[])]
                if entry_id
                else [ClaimSource(entry_id=f"microfact-{fact.id}", spans=[])]
            )
        )

        manifest_entry = manifest_index.get(entry_id) if entry_id else None
        source_hash = entry.source_hash if entry and entry.source_hash else None

        normalized_ids: list[str] = []
        if entry_id:
            normalized_ids = [entry_id]
        elif entry_by_id:
            normalized_ids = [next(iter(entry_by_id.keys()))]

        evidence_hashes = [source_hash] if source_hash else []
        manifest_hashes = [manifest_entry.hash] if manifest_entry else []

        raw_claim = {
            "id": f"microfact.{fact.id}",
            "type": "preference",
            "subject": fact.id,
            "predicate": "insight",
            "value": fact.statement,
            "statement": fact.statement,
            "scope": scope.model_dump(mode="python"),
            "strength": fact.confidence,
            "status": "tentative",
            "method": "inferred",
            "review_after_days": 90,
            "provenance": {
                "sources": [source.model_dump(mode="python") for source in provenance_sources],
                "first_seen": fact.first_seen or time_utils.created_date(timestamp),
                "last_updated": fact.last_seen or timestamp,
                "observation_count": 1,
            },
        }

        try:
            claim_model = normalization.normalize_claim_atom(
                raw_claim,
                timestamp=timestamp,
                default_sources=provenance_sources,
            )
        except (ValidationError, ValueError):
            continue

        proposals.append(
            ClaimProposal(
                claim=claim_model,
                normalized_ids=normalized_ids,
                evidence_hashes=evidence_hashes,
                manifest_hashes=manifest_hashes,
                rationale=f"Derived from micro-fact {fact.id}",
            )
        )
    return proposals


def normalize_claim_proposals(
    raw_claims: Iterable[Any],
    *,
    normalized_ids: list[str],
    evidence_hashes: list[str],
    manifest_hashes: list[str],
    default_sources: Sequence[ClaimSource],
    timestamp: str,
) -> list[ClaimProposal]:
    proposals: list[ClaimProposal] = []
    for raw in raw_claims:
        if isinstance(raw, ClaimProposal):
            claim_model = raw.claim.model_copy(deep=True)
            proposals.append(
                ClaimProposal(
                    claim=claim_model,
                    normalized_ids=merge_unique(raw.normalized_ids, normalized_ids),
                    evidence_hashes=merge_unique(raw.evidence_hashes, evidence_hashes),
                    manifest_hashes=merge_unique(raw.manifest_hashes, manifest_hashes),
                    rationale=raw.rationale,
                ),
            )
            continue
        sketch_like, metadata = _coerce_claim_sketch(raw)
        if sketch_like is None:
            continue

        if isinstance(sketch_like, ClaimSketch):
            sketch = sketch_like
        else:
            try:
                sketch = ClaimSketch.model_validate(sketch_like.model_dump(mode="python"))
            except ValidationError:
                continue

        if metadata.rationale is None:
            metadata.rationale = sketch.rationale

        payload_normalized_ids = merge_unique(sketch.normalized_ids, metadata.normalized_ids)
        payload_evidence_hashes = merge_unique(sketch.evidence_hashes, metadata.evidence_hashes)
        payload_manifest_hashes = merge_unique(sketch.manifest_hashes, metadata.manifest_hashes)

        try:
            claim_model = normalization.normalize_claim_atom(
                sketch.model_dump(mode="python"),
                timestamp=timestamp,
                default_sources=default_sources,
            )
        except (ValidationError, ValueError):
            continue

        proposals.append(
            ClaimProposal(
                claim=claim_model,
                normalized_ids=merge_unique(payload_normalized_ids, normalized_ids),
                evidence_hashes=merge_unique(payload_evidence_hashes, evidence_hashes),
                manifest_hashes=merge_unique(payload_manifest_hashes, manifest_hashes),
                rationale=metadata.rationale,
            ),
        )
    return proposals


class _SketchMetadata:
    __slots__ = ("normalized_ids", "evidence_hashes", "manifest_hashes", "rationale")

    def __init__(
        self,
        *,
        normalized_ids: Iterable[str] = (),
        evidence_hashes: Iterable[str] = (),
        manifest_hashes: Iterable[str] = (),
        rationale: str | None = None,
    ) -> None:
        self.normalized_ids = list(normalized_ids)
        self.evidence_hashes = list(evidence_hashes)
        self.manifest_hashes = list(manifest_hashes)
        self.rationale = rationale


def _coerce_claim_sketch(raw: Any) -> tuple[ClaimSketch | ClaimAtom | None, _SketchMetadata]:
    metadata = _SketchMetadata()

    if isinstance(raw, ClaimSketch):
        metadata = _SketchMetadata(
            normalized_ids=raw.normalized_ids,
            evidence_hashes=raw.evidence_hashes,
            manifest_hashes=raw.manifest_hashes,
            rationale=raw.rationale,
        )
        return raw, metadata

    if isinstance(raw, ClaimProposalPayload):
        metadata = _SketchMetadata(
            normalized_ids=raw.normalized_ids,
            evidence_hashes=raw.evidence_hashes,
            manifest_hashes=raw.manifest_hashes,
            rationale=raw.rationale,
        )
        return raw.claim, metadata

    if hasattr(raw, "model_dump"):
        payload = raw.model_dump(mode="python")
    else:
        payload = raw

    if isinstance(payload, dict):
        claim_data = payload.get("claim") or payload
        metadata = _SketchMetadata(
            normalized_ids=payload.get("normalized_ids") or [],
            evidence_hashes=payload.get("evidence_hashes") or [],
            manifest_hashes=payload.get("manifest_hashes") or [],
            rationale=str(payload.get("rationale") or payload.get("reason") or "").strip() or None,
        )
        try:
            sketch = ClaimSketch.model_validate(claim_data)
        except ValidationError:
            return None, _SketchMetadata()
        return sketch, metadata

    return None, _SketchMetadata()


def generate_microfacts(
    entries: Sequence[NormalizedEntry],
    date: str,
    *,
    use_fake_llm: bool,
    structured_call: StructuredCall,
    request_factory: FactsRequestFactory,
    retries: int,
    context: tuple[list[str], list[str], list[str], list[ClaimSource]],
    manifest_index: dict[str, ManifestEntry],
) -> MicroFactsFile:
    """Build a `MicroFactsFile` containing facts and claim proposals."""

    normalized_ids, evidence_hashes, manifest_hashes, default_sources = context
    manifest_index = manifest_index or {}
    claim_timestamp = time_utils.format_timestamp(time_utils.now())

    raw_claim_candidates: Iterable[Any] = []
    if use_fake_llm:
        facts_model = MicroFactsFile(facts=fake_microfacts(entries))
        if facts_model.claim_proposals:
            raw_claim_candidates = [
                proposal.model_dump(mode="python") for proposal in facts_model.claim_proposals
            ]
    else:
        response = cast(
            ExtractedFactsResponse,
            structured_call(request_factory, retries=retries, label=f"facts {date}"),
        )
        facts_model = MicroFactsFile(
            facts=[
                MicroFact(
                    id=fact.id,
                    statement=fact.statement,
                    confidence=float(fact.confidence),
                    evidence=fact.evidence.model_copy(deep=True),
                    first_seen=fact.first_seen,
                    last_seen=fact.last_seen,
                )
                for fact in response.facts
            ],
        )
        raw_claim_candidates = [
            proposal.model_dump(mode="python") for proposal in response.claim_proposals
        ]

    llm_claims = normalize_claim_proposals(
        raw_claims=raw_claim_candidates,
        normalized_ids=normalized_ids,
        evidence_hashes=evidence_hashes,
        manifest_hashes=manifest_hashes,
        default_sources=default_sources,
        timestamp=claim_timestamp,
    )

    derived_claims = _microfact_claim_proposals(
        facts_model.facts,
        entries=entries,
        manifest_index=manifest_index,
        timestamp=claim_timestamp,
    )

    combined: list[ClaimProposal] = []
    seen_ids: set[str] = set()
    for proposal in llm_claims:
        claim_id = proposal.claim.id
        if claim_id in seen_ids:
            continue
        combined.append(proposal)
        seen_ids.add(claim_id)

    for proposal in derived_claims:
        claim_id = proposal.claim.id
        if claim_id in seen_ids:
            continue
        combined.append(proposal)
        seen_ids.add(claim_id)

    facts_model.claim_proposals = combined

    return facts_model


__all__ = [
    "generate_microfacts",
    "merge_unique",
    "normalize_claim_proposals",
]
