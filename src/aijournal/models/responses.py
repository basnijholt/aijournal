"""LLM response models and payload schemas for aijournal."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .authoritative import JsonValue
from .base import AijournalModel
from .claim_atoms import ClaimAtom
from .derived import (
    AdviceReference,
    FactEvidence,
    ProfileSuggestionUpdate,
)


class DailySummaryResponse(AijournalModel):
    """Structured LLM response for daily summaries."""

    day: str
    bullets: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    todo_candidates: list[str] = Field(default_factory=list)


class ClaimSketch(AijournalModel):
    """Minimal claim payload emitted directly by the LLM."""

    id: str | None = None
    statement: str
    value: str | None = None
    rationale: str | None = None
    normalized_ids: list[str] = Field(default_factory=list)
    evidence_hashes: list[str] = Field(default_factory=list)
    manifest_hashes: list[str] = Field(default_factory=list)
    confidence: float | None = None
    strength: float | None = None
    type: str | None = None
    subject: str | None = None
    predicate: str | None = None
    scope: dict[str, Any] | None = None
    status: str | None = None
    method: str | None = None
    user_verified: bool | None = None
    review_after_days: int | None = None
    provenance: dict[str, Any] | None = None


class ExtractedFactPayload(AijournalModel):
    """Structured micro-fact emitted by the extraction pipeline."""

    id: str
    statement: str
    confidence: float
    evidence: FactEvidence
    first_seen: str | None = None
    last_seen: str | None = None


class ClaimProposalPayload(AijournalModel):
    """Structured claim proposal returned directly by the LLM."""

    claim: ClaimSketch
    normalized_ids: list[str] = Field(default_factory=list)
    evidence_hashes: list[str] = Field(default_factory=list)
    manifest_hashes: list[str] = Field(default_factory=list)
    rationale: str | None = None


class ExtractedFactsResponse(AijournalModel):
    """Structured response for the micro-facts pipeline."""

    facts: list[ExtractedFactPayload] = Field(default_factory=list)
    claim_proposals: list[ClaimProposalPayload] = Field(default_factory=list)


class FacetProposalPayload(AijournalModel):
    """Structured facet proposal produced during characterization."""

    path: str | None = None
    value: JsonValue = None
    operation: str | None = None
    method: str | None = None
    confidence: float | None = None
    review_after_days: int | None = None
    user_verified: bool | None = None
    normalized_ids: list[str] = Field(default_factory=list)
    evidence_hashes: list[str] = Field(default_factory=list)
    rationale: str | None = None


class CharacterizeResponse(AijournalModel):
    """Structured response for profile characterization."""

    claims: list[ClaimProposalPayload] = Field(default_factory=list)
    facets: list[FacetProposalPayload] = Field(default_factory=list)
    interview_prompts: list[str] = Field(default_factory=list)


class ProfileSuggestionUpsertPayload(AijournalModel):
    """Structured upsert proposal returned by profile suggestions."""

    target: str = "claims"
    operation: str = "upsert"
    value: ClaimAtom | dict[str, Any]
    rationale: str | None = None


class ProfileSuggestionsResponse(AijournalModel):
    """Structured response for profile suggestions."""

    upserts: list[ProfileSuggestionUpsertPayload] = Field(default_factory=list)
    updates: list[ProfileSuggestionUpdate] = Field(default_factory=list)


class SimpleSuggestion(AijournalModel):
    """Flattened suggestion item returned by the simplified LLM schema."""

    kind: str
    id: str | None = None
    statement: str | None = None
    facet_path: str | None = None
    value: JsonValue | None = None
    rationale: str | None = None
    evidence: list[str] = Field(default_factory=list)
    status: str | None = None
    confidence: float | None = None


class SimpleProfileSuggestionsResponse(AijournalModel):
    """Simplified schema returned directly by the LLM."""

    suggestions: list[SimpleSuggestion] = Field(default_factory=list)


class AdviceLLMRecommendation(AijournalModel):
    """Simplified recommendation payload emitted directly by the LLM."""

    title: str
    why_this_fits_you: AdviceReference = Field(default_factory=AdviceReference)
    steps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    mitigations: list[str] = Field(default_factory=list)


class AdviceLLMResponse(AijournalModel):
    """Minimal advice-card schema expected from the live LLM."""

    id: str
    query: str
    assumptions: list[str] = Field(default_factory=list)
    recommendations: list[AdviceLLMRecommendation] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    alignment: AdviceReference = Field(default_factory=AdviceReference)
    style: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AdviceLLMRecommendation",
    "AdviceLLMResponse",
    "CharacterizeResponse",
    "ClaimProposalPayload",
    "ClaimSketch",
    "DailySummaryResponse",
    "ExtractedFactPayload",
    "ExtractedFactsResponse",
    "FacetProposalPayload",
    "ProfileSuggestionUpsertPayload",
    "ProfileSuggestionsResponse",
    "SimpleProfileSuggestionsResponse",
    "SimpleSuggestion",
]
