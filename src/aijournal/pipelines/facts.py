"""Pipeline helpers for generating micro-facts and claim proposals."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from aijournal.domain.changes import ClaimAtomInput, ClaimProposal
from aijournal.domain.claims import (
    ClaimAtom,
    ClaimSource,
    ClaimSourceSpan,
    Scope,
)
from aijournal.domain.evidence import SourceRef, redact_source_text
from aijournal.domain.facts import MicroFact, MicroFactsFile
from aijournal.fakes import fake_microfacts
from aijournal.pipelines import normalization
from aijournal.services.microfacts.index import MicrofactIndex, MicrofactRecord
from aijournal.utils import time as time_utils

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from aijournal.domain.journal import NormalizedEntry
    from aijournal.models.authoritative import ManifestEntry


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
    return f"{proposal.type}|{proposal.subject}|{proposal.predicate}|{proposal.value}|{proposal.statement}"


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
            ),
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

        claim_input = ClaimAtomInput(
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
        )

        proposals.append(
            ClaimProposal(
                type=claim_input.type,
                subject=claim_input.subject,
                predicate=claim_input.predicate,
                value=claim_input.value,
                statement=claim_input.statement,
                scope=claim_input.scope,
                strength=claim_input.strength,
                status=claim_input.status,
                method=claim_input.method,
                user_verified=claim_input.user_verified,
                review_after_days=claim_input.review_after_days,
                normalized_ids=normalized_ids,
                evidence=[
                    SourceRef.model_validate(src.model_dump(mode="python"))
                    for src in provenance_sources
                ],
                manifest_hashes=manifest_hashes,
                rationale=f"Derived from micro-fact {fact.id}",
            ),
        )
    return proposals


def _entries_by_id(entries: Sequence[NormalizedEntry]) -> dict[str, NormalizedEntry]:
    lookup: dict[str, NormalizedEntry] = {}
    for entry in entries:
        if entry.id:
            lookup[entry.id] = entry
    return lookup


def _lexical_overlap(text_a: str, text_b: str) -> float:
    tokens_a = {token for token in text_a.split() if token}
    tokens_b = {token for token in text_b.split() if token}
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a.intersection(tokens_b)
    union = tokens_a.union(tokens_b)
    return len(intersection) / len(union)


def _should_merge_records(
    candidate: MicrofactRecord,
    existing: MicrofactRecord,
    *,
    distance: float | None,
    threshold: float,
    min_overlap: float,
) -> bool:
    if candidate.uid == existing.uid:
        return True
    if candidate.domain and existing.domain and candidate.domain != existing.domain:
        return False
    if candidate.contexts and existing.contexts:
        if not set(candidate.contexts).intersection(existing.contexts):
            return False
    if candidate.canonical_statement == existing.canonical_statement:
        return True
    lexical = _lexical_overlap(candidate.canonical_statement, existing.canonical_statement)
    if lexical < min_overlap:
        return False
    if distance is None:
        return False
    return distance <= threshold


def _consolidate_microfacts(
    facts: Sequence[MicroFact],
    *,
    entries: Sequence[NormalizedEntry],
    date: str,
    index: MicrofactIndex,
) -> None:
    entry_by_id = _entries_by_id(entries)
    settings = index.settings
    for fact in facts:
        entry = None
        entry_id = fact.evidence.entry_id if fact.evidence else None
        if entry_id:
            entry = entry_by_id.get(entry_id)
        scope = _scope_from_fact(fact, entry)
        fact_key = f"{date}:{fact.id}"
        record = MicrofactRecord.from_microfact(
            day=date,
            fact=fact,
            domain=scope.domain,
            contexts=scope.context,
        )
        matches = index.query_similar(record.statement, top_k=settings.default_top_k)
        merged = False
        for match in matches:
            existing = MicrofactRecord.from_match(match)
            if existing is None:
                continue
            if fact_key in existing.source_fact_ids:
                existing.apply_to_fact(fact)
                merged = True
                break
            if not _should_merge_records(
                record,
                existing,
                distance=match.distance,
                threshold=settings.merge_distance,
                min_overlap=settings.min_token_overlap,
            ):
                continue
            existing.merge_observation(
                confidence=fact.confidence,
                date=date,
                fact_id=fact.id,
                evidence_entry=entry_id,
                max_evidence_entries=settings.max_evidence_entries,
                fact_key=fact_key,
            )
            existing.apply_to_fact(fact)
            index.upsert([existing])
            merged = True
            break
        if not merged:
            record.apply_to_fact(fact)
            index.upsert([record])


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

        claim_input_for_normalize = ClaimAtomInput(
            type=proposal.type,
            subject=proposal.subject,
            predicate=proposal.predicate,
            value=proposal.value,
            statement=proposal.statement,
            scope=proposal.scope,
            strength=proposal.strength,
            status=proposal.status,
            method=proposal.method,
            user_verified=proposal.user_verified,
            review_after_days=proposal.review_after_days,
        )

        claim_atom = _normalize_claim_input(
            claim_input_for_normalize,
            timestamp=timestamp,
            default_sources=default_sources,
            evidence=proposal.evidence,
        )

        claim_input = ClaimAtomInput(
            type=claim_atom.type,
            subject=claim_atom.subject,
            predicate=claim_atom.predicate,
            value=claim_atom.value,
            statement=claim_atom.statement,
            scope=claim_atom.scope,
            strength=claim_atom.strength,
            status=claim_atom.status,
            method=claim_atom.method,
            user_verified=claim_atom.user_verified,
            review_after_days=claim_atom.review_after_days,
        )

        combined_sources = _merge_sources(default_sources, proposal.evidence)

        sanitized_sources = [
            SourceRef.model_validate(
                redact_source_text(src).model_dump(mode="python"),
            )
            for src in combined_sources
        ]

        scoped_ids = proposal.normalized_ids or normalized_ids
        scoped_hashes = proposal.manifest_hashes or manifest_hashes

        proposals.append(
            ClaimProposal(
                type=claim_input.type,
                subject=claim_input.subject,
                predicate=claim_input.predicate,
                value=claim_input.value,
                statement=claim_input.statement,
                scope=claim_input.scope,
                strength=claim_input.strength,
                status=claim_input.status,
                method=claim_input.method,
                user_verified=claim_input.user_verified,
                review_after_days=claim_input.review_after_days,
                normalized_ids=merge_unique(scoped_ids, []),
                evidence=sanitized_sources,
                manifest_hashes=merge_unique(scoped_hashes, []),
                rationale=proposal.rationale,
            ),
        )

    return proposals


def _normalize_claim_input(
    claim_input: ClaimAtomInput,
    *,
    timestamp: str,
    default_sources: Sequence[ClaimSource],
    evidence: Sequence[SourceRef],
) -> ClaimAtom:
    combined_sources = _merge_sources(default_sources, evidence)
    claim_dict = claim_input.model_dump(mode="python")
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
    llm_microfacts: MicroFactsFile | None,
    context: tuple[list[str], list[str], list[ClaimSource]],
    manifest_index: dict[str, ManifestEntry],
    microfact_index: MicrofactIndex | None = None,
) -> MicroFactsFile:
    """Build a `MicroFactsFile` containing facts and claim proposals."""
    normalized_ids, manifest_hashes, default_sources = context
    manifest_index = manifest_index or {}
    claim_timestamp = time_utils.format_timestamp(time_utils.now())

    if use_fake_llm:
        llm_microfacts = MicroFactsFile(facts=fake_microfacts(entries))
    elif llm_microfacts is None:
        msg = "llm_microfacts must be provided when fake mode is disabled"
        raise ValueError(msg)

    facts_model = MicroFactsFile.model_validate(llm_microfacts.model_dump(mode="python"))
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

    if microfact_index is not None and facts_model.facts:
        _consolidate_microfacts(
            facts_model.facts,
            entries=entries,
            date=date,
            index=microfact_index,
        )

    return facts_model
