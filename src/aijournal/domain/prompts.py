"""Lightweight DTOs for LLM-facing prompts (characterize, profile_suggest)."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from aijournal.common.base import StrictModel
from aijournal.domain.claims import Scope
from aijournal.domain.enums import ClaimMethod, ClaimStatus, ClaimType, FacetOperation
from aijournal.domain.evidence import SourceRef, Span


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
        raise ValueError(f"reason must be ≤{limit} words (got {len(words)})")
    return value


class PromptClaimItem(StrictModel):
    """Lightweight claim item that LLM emits (no system metadata)."""

    type: ClaimType
    statement: str = Field(..., max_length=160)
    subject: str | None = Field(default=None, max_length=80)
    predicate: str | None = Field(default=None, max_length=80)
    value: str | None = Field(default=None, max_length=160)
    reason: str | None = None
    evidence_entry: str | None = None
    evidence_para: int = Field(default=0, ge=0)

    @field_validator("statement", mode="before")
    @classmethod
    def _strip_required(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
        if not value:
            raise ValueError("statement cannot be empty")
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
            raise ValueError("path cannot be empty")
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
            raise ValueError("value required for set/merge operations")
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

    return ClaimProposal(
        type=item.type,
        subject=subject,
        predicate=predicate,
        value=value,
        statement=item.statement,
        scope=Scope(),  # Empty scope by default
        strength=0.55,  # Default confidence
        status=ClaimStatus.TENTATIVE,  # Default status
        method=ClaimMethod.INFERRED,  # Default method
        user_verified=False,
        review_after_days=120,
        normalized_ids=normalized_ids,
        evidence=evidence,
        manifest_hashes=manifest_hashes,
        rationale=item.reason,
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
) -> Any:  # Returns ProfileUpdateProposals
    """Convert lightweight prompt DTOs to full domain models with system metadata."""
    from aijournal.domain.changes import ProfileUpdateProposals

    claims = [
        convert_prompt_claim_to_proposal(
            item,
            normalized_ids=normalized_ids,
            manifest_hashes=manifest_hashes,
        )
        for item in prompt_updates.claims
    ]

    facets = [convert_prompt_facet_to_change(item) for item in prompt_updates.facets]

    return ProfileUpdateProposals(
        claims=claims,
        facets=facets,
        interview_prompts=prompt_updates.interview_prompts,
    )
