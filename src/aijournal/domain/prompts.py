"""Lightweight DTOs for LLM-facing profile_update and extract_facts prompts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field, field_validator

from aijournal.common.base import StrictModel
from aijournal.domain.claims import Scope
from aijournal.domain.enums import ClaimMethod, ClaimStatus, ClaimType, FacetOperation
from aijournal.domain.evidence import SourceRef, Span

if TYPE_CHECKING:
    from collections.abc import Mapping


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _enforce_word_limit(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    words = value.split()
    if len(words) > limit:
        msg = f"reason must be ≤{limit} words (got {len(words)})"
        raise ValueError(msg)
    return value


class PromptClaimItem(StrictModel):
    """Lightweight claim item that LLM emits (no system metadata)."""

    type: ClaimType
    statement: str = Field(..., max_length=160)
    subject: str | None = Field(default=None, max_length=80)
    predicate: str | None = Field(default=None, max_length=80)
    value: str | None = Field(default=None, max_length=160)
    strength: float | None = Field(default=None, ge=0.0, le=1.0)
    status: ClaimStatus | None = None
    method: ClaimMethod | None = None
    scope_domain: str | None = None
    scope_context: list[str] | None = None
    scope_conditions: list[str] | None = None
    reason: str | None = None
    evidence_entry: str | None = None
    evidence_para: int = Field(default=0, ge=0)

    @field_validator("statement", mode="before")
    @classmethod
    def _strip_required(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
        if not value:
            msg = "statement cannot be empty"
            raise ValueError(msg)
        return value

    @field_validator("subject", "predicate", mode="before")
    @classmethod
    def _strip_optional(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
        return value or None

    @field_validator("value", mode="after")
    @classmethod
    def _strip_value(cls, value: str | None) -> str | None:
        return _clean_text(value)

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str | None) -> str | None:
        value = _clean_text(value)
        return _enforce_word_limit(value, limit=25)


class PromptFacetItem(StrictModel):
    """Lightweight facet change that LLM emits (no system metadata)."""

    path: str
    operation: FacetOperation
    value: Any | None = None
    reason: str | None = None
    evidence_entry: str | None = None
    evidence_para: int = Field(default=0, ge=0)

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        text = value.strip()
        if not text:
            msg = "path cannot be empty"
            raise ValueError(msg)
        return text

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str | None) -> str | None:
        value = _clean_text(value)
        return _enforce_word_limit(value, limit=25)

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: Any | None, info: Any) -> Any | None:
        operation = info.data.get("operation")
        if operation in {FacetOperation.SET, FacetOperation.MERGE} and value is None:
            msg = "value required for set/merge operations"
            raise ValueError(msg)
        return value


class PromptProfileUpdates(StrictModel):
    """Container for LLM-emitted profile updates (lightweight DTOs only)."""

    claims: list[PromptClaimItem] = Field(default_factory=list)
    facets: list[PromptFacetItem] = Field(default_factory=list)
    interview_prompts: list[str] = Field(default_factory=list)


# Conversion functions to full domain models


def convert_prompt_claim_to_proposal(
    item: PromptClaimItem,
    *,
    normalized_ids: list[str],
    manifest_hashes: list[str],
) -> Any:  # Returns ClaimProposal (avoiding circular import)
    """Convert lightweight prompt DTO to ClaimProposal with system metadata."""
    from aijournal.domain.changes import ClaimProposal

    # Fill in defaults for missing optional fields
    subject = item.subject or "self"
    predicate = item.predicate or "states"
    value = item.value or item.statement

    # Build evidence from simple references
    evidence: list[SourceRef] = []
    if item.evidence_entry:
        span = Span(type="para", index=item.evidence_para)
        source = SourceRef(entry_id=item.evidence_entry, spans=[span])
        evidence = [source]

    scope = Scope(
        domain=(item.scope_domain or None),
        context=[s.strip() for s in (item.scope_context or []) if s.strip()],
        conditions=[s.strip() for s in (item.scope_conditions or []) if s.strip()],
    )

    strength = float(item.strength) if item.strength is not None else 0.55
    status = item.status or ClaimStatus.TENTATIVE
    method = item.method or ClaimMethod.INFERRED

    return ClaimProposal(
        type=item.type,
        subject=subject,
        predicate=predicate,
        value=value,
        statement=item.statement,
        scope=scope,
        strength=strength,
        status=status,
        method=method,
        user_verified=False,
        review_after_days=120,
        normalized_ids=normalized_ids,
        evidence=evidence,
        manifest_hashes=manifest_hashes,
        rationale=item.reason,
    )


class PromptMicroFact(StrictModel):
    """Lightweight micro-fact emitted by the LLM."""

    id: str
    statement: str = Field(..., max_length=500)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_entry: str | None = None
    evidence_para: int = Field(default=0, ge=0)
    first_seen: str | None = None
    last_seen: str | None = None

    @field_validator("statement", mode="before")
    @classmethod
    def _strip_statement(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
        if not value:
            msg = "statement cannot be empty"
            raise ValueError(msg)
        return value


class PromptMicroFacts(StrictModel):
    """Container for LLM-emitted micro-facts and optional claim proposals."""

    facts: list[PromptMicroFact] = Field(default_factory=list)
    claim_proposals: list[PromptClaimItem] = Field(default_factory=list)


def _source_from_prompt_fact(item: PromptMicroFact) -> SourceRef:
    spans: list[Span] = []
    if item.evidence_entry:
        spans.append(Span(type="para", index=item.evidence_para))
    entry_id = item.evidence_entry or f"prompt.fact.{item.id}"
    return SourceRef(entry_id=entry_id, spans=spans)


_METADATA_ID_HINTS = (
    "entry-created",
    "frontmatter",
    "metadata",
    "title-",
    "tags-",
    "mood-",
    "slug",
)

_METADATA_STATEMENT_HINTS = (
    "entry created",  # e.g. "Entry created on 2025-11-14"
    "title is",
    "tags include",
    "tagged",
    "front matter",
    "metadata",
    "slug",
)


def is_metadata_only_fact(item: PromptMicroFact) -> bool:
    """Heuristic filter for metadata-only micro-facts.

    Drops statements that merely restate front-matter (created_at, title, tags, mood)
    or that omit an `evidence_entry`, which means the fact cannot be grounded in a
    specific paragraph.
    """
    identifier = item.id.lower()
    statement = item.statement.lower()

    if not item.evidence_entry:
        return True

    if any(hint in identifier for hint in _METADATA_ID_HINTS):
        return True

    return bool(any(hint in statement for hint in _METADATA_STATEMENT_HINTS))


def convert_prompt_microfacts(prompt: PromptMicroFacts) -> Any:  # Returns MicroFactsFile
    """Convert lightweight prompt DTO to the authoritative micro-facts payload."""
    from aijournal.domain.facts import MicroFact, MicroFactsFile

    filtered_facts = [fact for fact in prompt.facts if not is_metadata_only_fact(fact)]

    facts = [
        MicroFact(
            id=item.id,
            statement=item.statement,
            confidence=float(item.confidence) if item.confidence is not None else 0.6,
            evidence=_source_from_prompt_fact(item),
            first_seen=item.first_seen,
            last_seen=item.last_seen,
        )
        for item in filtered_facts
    ]

    claim_proposals = [
        convert_prompt_claim_to_proposal(
            proposal,
            normalized_ids=[proposal.evidence_entry] if proposal.evidence_entry else [],
            manifest_hashes=[],
        )
        for proposal in prompt.claim_proposals
    ]

    return MicroFactsFile(
        facts=facts,
        claim_proposals=claim_proposals,
    )


def convert_prompt_facet_to_change(item: PromptFacetItem) -> Any:  # Returns FacetChange
    """Convert lightweight prompt DTO to full FacetChange with system metadata."""
    from aijournal.domain.changes import FacetChange

    # Build evidence from simple references
    evidence: list[SourceRef] = []
    if item.evidence_entry:
        span = Span(type="para", index=item.evidence_para)
        source = SourceRef(entry_id=item.evidence_entry, spans=[span])
        evidence = [source]

    return FacetChange(
        path=item.path,
        operation=item.operation,
        value=item.value,
        method="inferred",  # Default method
        confidence=0.55,  # Default confidence
        review_after_days=120,
        user_verified=False,
        evidence=evidence,
        rationale=item.reason,
    )


def convert_prompt_updates_to_proposals(
    prompt_updates: PromptProfileUpdates,
    *,
    normalized_ids: list[str],
    manifest_hashes: list[str],
    entry_hash_lookup: Mapping[str, str] | None = None,
) -> Any:  # Returns ProfileUpdateProposals
    """Convert lightweight prompt DTOs to full domain models with system metadata."""
    from aijournal.domain.changes import ProfileUpdateProposals

    def _claim_context(item: PromptClaimItem) -> tuple[list[str], list[str]]:
        entry_id = (item.evidence_entry or "").strip()
        if entry_id:
            hashes: list[str] = []
            if entry_hash_lookup:
                manifest_hash = entry_hash_lookup.get(entry_id)
                if manifest_hash:
                    hashes = [manifest_hash]
            return [entry_id], hashes

        return list(normalized_ids), list(manifest_hashes)

    claims = []
    for item in prompt_updates.claims:
        claim_ids, claim_hashes = _claim_context(item)
        claims.append(
            convert_prompt_claim_to_proposal(
                item,
                normalized_ids=claim_ids,
                manifest_hashes=claim_hashes,
            ),
        )

    facets = [convert_prompt_facet_to_change(item) for item in prompt_updates.facets]

    return ProfileUpdateProposals(
        claims=claims,
        facets=facets,
        interview_prompts=prompt_updates.interview_prompts,
    )
