"""Pipeline helpers for generating micro-facts and claim proposals."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any, cast

from pydantic import ValidationError

from aijournal.domain.changes import ClaimProposal
from aijournal.domain.claims import (
    ClaimAtom,
    ClaimSource,
    ClaimSourceSpan,
    Scope,
)
from aijournal.domain.evidence import SourceRef, redact_source_text
from aijournal.domain.facts import MicroFact, MicroFactsFile
from aijournal.domain.journal import NormalizedEntry
from aijournal.fakes import fake_microfacts
from aijournal.models.authoritative import ManifestEntry
from aijournal.pipelines import normalization
from aijournal.utils import time as time_utils

StructuredCall = Callable[..., Any]
FactsRequestFactory = Callable[[], MicroFactsFile]


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


def _proposal_key(proposal: ClaimProposal) -> str:
    return "|".join(
        [
            proposal.type,
            proposal.subject,
            proposal.predicate,
            proposal.value or proposal.statement,
            proposal.statement,
        ]
    )


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

        normalized_ids: list[str] = []
        if entry_id:
            normalized_ids = [entry_id]
        elif entry_by_id:
            normalized_ids = [next(iter(entry_by_id.keys()))]

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
                type=claim_model.type,
                subject=claim_model.subject,
                predicate=claim_model.predicate,
                value=claim_model.value,
                statement=claim_model.statement,
                scope=claim_model.scope,
                strength=claim_model.strength,
                status=claim_model.status,
                method=claim_model.method,
                user_verified=claim_model.user_verified,
                review_after_days=claim_model.review_after_days,
                normalized_ids=normalized_ids,
                evidence=[
                    SourceRef.model_validate(src.model_dump(mode="python"))
                    for src in provenance_sources
                ],
                manifest_hashes=manifest_hashes,
                evidence_entry=entry_id,
                reason=f"Derived from micro-fact {fact.id}",
            )
        )
    return proposals


def normalize_claim_proposals(
    raw_claims: Iterable[Any],
    *,
    normalized_ids: list[str],
    manifest_hashes: list[str],
    default_sources: Sequence[ClaimSource],
    timestamp: str,
) -> list[ClaimProposal]:
    proposals: list[ClaimProposal] = []
    for raw in raw_claims:
        try:
            proposal = raw if isinstance(raw, ClaimProposal) else ClaimProposal.model_validate(raw)
        except ValidationError:
            continue

        claim_atom = _normalize_claim_fields(
            proposal.claim_fields(),
            timestamp=timestamp,
            default_sources=default_sources,
            evidence=proposal.evidence,
        )

        combined_sources = _merge_sources(default_sources, proposal.evidence)

        sanitized_sources = [
            SourceRef.model_validate(
                redact_source_text(src).model_dump(mode="python"),
            )
            for src in combined_sources
        ]

        proposal.subject = claim_atom.subject
        proposal.predicate = claim_atom.predicate
        proposal.value = claim_atom.value
        proposal.statement = claim_atom.statement
        proposal.scope = claim_atom.scope
        proposal.strength = claim_atom.strength
        proposal.status = claim_atom.status
        proposal.method = claim_atom.method
        proposal.user_verified = claim_atom.user_verified
        proposal.review_after_days = claim_atom.review_after_days
        proposal.evidence = sanitized_sources
        proposal.normalized_ids = merge_unique(proposal.normalized_ids, normalized_ids)
        proposal.manifest_hashes = merge_unique(proposal.manifest_hashes, manifest_hashes)
        proposals.append(proposal)

    return proposals


def _normalize_claim_fields(
    claim_fields: dict[str, Any],
    *,
    timestamp: str,
    default_sources: Sequence[ClaimSource],
    evidence: Sequence[SourceRef],
) -> ClaimAtom:
    combined_sources = _merge_sources(default_sources, evidence)
    claim_dict = dict(claim_fields)
    return normalization.normalize_claim_atom(
        claim_dict,
        timestamp=timestamp,
        default_sources=combined_sources,
    )


def _merge_sources(
    existing: Sequence[ClaimSource],
    extras: Sequence[SourceRef],
) -> list[ClaimSource]:
    merged: list[ClaimSource] = []
    seen: set[tuple[str, tuple[tuple[str | None, int | None, int | None, int | None], ...]]] = set()

    def key(
        source: SourceRef,
    ) -> tuple[str, tuple[tuple[str | None, int | None, int | None, int | None], ...]]:
        span_key = tuple(
            (span.type, span.index, span.start, span.end) for span in source.spans or []
        )
        return source.entry_id, span_key

    for source in list(existing) + list(extras):
        candidate = redact_source_text(SourceRef.model_validate(source.model_dump(mode="python")))
        identifier = key(candidate)
        if identifier in seen:
            continue
        seen.add(identifier)
        merged.append(ClaimSource.model_validate(candidate.model_dump(mode="python")))
    return merged


def generate_microfacts(
    entries: Sequence[NormalizedEntry],
    date: str,
    *,
    use_fake_llm: bool,
    structured_call: StructuredCall,
    request_factory: FactsRequestFactory,
    retries: int,
    context: tuple[list[str], list[str], list[ClaimSource]],
    manifest_index: dict[str, ManifestEntry],
) -> MicroFactsFile:
    """Build a `MicroFactsFile` containing facts and claim proposals."""

    normalized_ids, manifest_hashes, default_sources = context
    manifest_index = manifest_index or {}
    claim_timestamp = time_utils.format_timestamp(time_utils.now())

    if use_fake_llm:
        generated = MicroFactsFile(facts=fake_microfacts(entries))
    else:
        response = cast(
            MicroFactsFile,
            structured_call(request_factory, retries=retries, label=f"facts {date}"),
        )
        generated = response

    facts_model = MicroFactsFile.model_validate(generated.model_dump(mode="python"))
    raw_claim_candidates: Iterable[Any] = [
        proposal.model_dump(mode="python") for proposal in facts_model.claim_proposals
    ]

    llm_claims = normalize_claim_proposals(
        raw_claims=raw_claim_candidates,
        normalized_ids=normalized_ids,
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
        key = _proposal_key(proposal)
        if key in seen_ids:
            continue
        combined.append(proposal)
        seen_ids.add(key)

    for proposal in derived_claims:
        key = _proposal_key(proposal)
        if key in seen_ids:
            continue
        combined.append(proposal)
        seen_ids.add(key)

    facts_model.claim_proposals = combined

    return facts_model
