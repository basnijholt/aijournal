"""Pydantic models describing aijournal's authoritative and derived artifacts."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .base import AijournalModel
from .claim_atoms import (
    ClaimAtom,
    ClaimAtomsFile,
    ClaimMethod,
    ClaimSource,
    ClaimSourceSpan,
    ClaimStatus,
    ClaimType,
    Provenance,
    Scope,
)

Claim = ClaimAtom


class JournalEntry(AijournalModel):
    """Human-authored Markdown entry metadata (PLAN §3.1)."""

    id: str
    created_at: str
    title: str
    tags: list[str] = Field(default_factory=list)
    mood: str | None = None
    projects: list[str] = Field(default_factory=list)
    summary: str | None = None


class JournalSection(AijournalModel):
    """Heading/section metadata extracted from Markdown."""

    heading: str
    level: int = 1
    summary: str | None = None
    para_index: int | None = None


class NormalizedEntity(AijournalModel):
    """Structured entity extracted during normalization."""

    type: str
    value: str
    extra: dict[str, Any] = Field(default_factory=dict)


class NormalizedEntry(AijournalModel):
    """Machine-readable normalized entry (PLAN §3.2)."""

    id: str
    created_at: str
    source_path: str
    title: str
    tags: list[str] = Field(default_factory=list)
    sections: list[JournalSection] = Field(default_factory=list)
    entities: list[NormalizedEntity] = Field(default_factory=list)
    summary: str | None = None
    source_hash: str | None = None
    source_type: str | None = None


class SummaryMeta(AijournalModel):
    llm_model: str = "unknown"
    prompt_path: str = ""
    prompt_hash: str | None = None
    created_at: str = ""


class DailySummary(AijournalModel):
    """Derived day summary (PLAN §4.1)."""

    day: str
    bullets: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    todo_candidates: list[str] = Field(default_factory=list)
    meta: SummaryMeta = Field(default_factory=SummaryMeta)


class FactEvidenceSpan(AijournalModel):
    type: str
    index: int | None = None
    start: int | None = None
    end: int | None = None
    text: str | None = None


class FactEvidence(AijournalModel):
    entry_id: str
    spans: list[FactEvidenceSpan] = Field(default_factory=list)


class MicroFact(AijournalModel):
    id: str
    statement: str
    confidence: float
    evidence: FactEvidence
    first_seen: str | None = None
    last_seen: str | None = None


class MicroFactsFile(AijournalModel):
    facts: list[MicroFact] = Field(default_factory=list)
    meta: SummaryMeta = Field(default_factory=SummaryMeta)


class ClaimsFile(AijournalModel):
    claims: list[ClaimAtom] = Field(default_factory=list)


class SelfProfile(AijournalModel):
    traits: dict[str, Any] = Field(default_factory=dict)
    values_motivations: dict[str, Any] = Field(default_factory=dict)
    goals: dict[str, Any] = Field(default_factory=dict)
    decision_style: dict[str, Any] = Field(default_factory=dict)
    affect_energy: dict[str, Any] = Field(default_factory=dict)
    social: dict[str, Any] = Field(default_factory=dict)
    boundaries_ethics: dict[str, Any] = Field(default_factory=dict)
    coaching_prefs: dict[str, Any] = Field(default_factory=dict)


class ProfileSuggestionUpsert(AijournalModel):
    target: str
    operation: str
    value: dict[str, Any]
    rationale: str | None = None


class ProfileSuggestionUpdate(AijournalModel):
    target: str
    operation: str
    value: Any
    method: str | None = None
    user_verified: bool | None = None
    evidence: list[str] | None = None
    rationale: str | None = None


class ProfileSuggestions(AijournalModel):
    upserts: list[ProfileSuggestionUpsert] = Field(default_factory=list)
    updates: list[ProfileSuggestionUpdate] = Field(default_factory=list)
    meta: SummaryMeta | None = None


class InterviewQuestion(AijournalModel):
    id: str
    text: str
    target_facet: str | None = None
    priority: str | None = None


class InterviewSet(AijournalModel):
    questions: list[InterviewQuestion] = Field(default_factory=list)
    meta: SummaryMeta | None = None


class AdviceReference(AijournalModel):
    facets: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)


class AdviceRecommendation(AijournalModel):
    title: str
    why_this_fits_you: AdviceReference = Field(default_factory=AdviceReference)
    steps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    mitigations: list[str] = Field(default_factory=list)


class AdviceCard(AijournalModel):
    id: str
    query: str
    assumptions: list[str] = Field(default_factory=list)
    recommendations: list[AdviceRecommendation] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    confidence: float | None = None
    alignment: AdviceReference = Field(default_factory=AdviceReference)
    style: dict[str, Any] = Field(default_factory=dict)
    meta: SummaryMeta = Field(default_factory=SummaryMeta)


class ProfileUpdateInput(AijournalModel):
    """Normalized entry metadata captured in a characterization batch."""

    id: str
    normalized_path: str
    source_hash: str | None = None
    manifest_hash: str | None = None
    tags: list[str] = Field(default_factory=list)


class ClaimProposal(AijournalModel):
    """Pending claim update enriched with provenance."""

    claim: ClaimAtom
    normalized_ids: list[str] = Field(default_factory=list)
    evidence_hashes: list[str] = Field(default_factory=list)
    manifest_hashes: list[str] = Field(default_factory=list)
    rationale: str | None = None


class FacetProposal(AijournalModel):
    """Pending facet update referencing profile paths."""

    path: str
    value: Any
    operation: str = "set"
    method: str | None = None
    confidence: float | None = None
    review_after_days: int | None = None
    user_verified: bool | None = None
    normalized_ids: list[str] = Field(default_factory=list)
    evidence_hashes: list[str] = Field(default_factory=list)
    rationale: str | None = None


class ProfileUpdateProposals(AijournalModel):
    """Aggregation of claim and facet proposals."""

    claims: list[ClaimProposal] = Field(default_factory=list)
    facets: list[FacetProposal] = Field(default_factory=list)


class ProfileUpdateBatch(AijournalModel):
    """Pending profile update batch emitted by `aijournal characterize`."""

    batch_id: str
    created_at: str
    date: str
    inputs: list[ProfileUpdateInput] = Field(default_factory=list)
    proposals: ProfileUpdateProposals = Field(default_factory=ProfileUpdateProposals)
    meta: SummaryMeta = Field(default_factory=SummaryMeta)


class PersonaCoreMeta(AijournalModel):
    generated_at: str
    token_budget: int
    planned_tokens: int
    char_per_token: float
    selection_strategy: str | None = None
    trimmed: list[dict[str, Any]] = Field(default_factory=list)
    claim_pool: int | None = None
    claim_count: int | None = None
    max_claims: int | None = None
    min_claims: int | None = None
    budget_exceeded: bool = False
    sources: dict[str, str] = Field(default_factory=dict)


class PersonaCore(AijournalModel):
    profile: dict[str, Any] = Field(default_factory=dict)
    claims: list[ClaimAtom] = Field(default_factory=list)


class PersonaCoreFile(AijournalModel):
    persona: PersonaCore = Field(default_factory=PersonaCore)
    meta: PersonaCoreMeta


__all__ = [
    "AdviceCard",
    "AdviceRecommendation",
    "AdviceReference",
    "AijournalModel",
    "Claim",
    "ClaimAtom",
    "ClaimAtomsFile",
    "ClaimMethod",
    "ClaimSource",
    "ClaimSourceSpan",
    "ClaimStatus",
    "ClaimType",
    "ClaimsFile",
    "DailySummary",
    "FactEvidence",
    "FactEvidenceSpan",
    "InterviewQuestion",
    "InterviewSet",
    "JournalEntry",
    "JournalSection",
    "MicroFact",
    "MicroFactsFile",
    "NormalizedEntity",
    "NormalizedEntry",
    "PersonaCore",
    "PersonaCoreFile",
    "PersonaCoreMeta",
    "ProfileSuggestionUpdate",
    "ProfileSuggestionUpsert",
    "ProfileSuggestions",
    "ProfileUpdateBatch",
    "ProfileUpdateInput",
    "ProfileUpdateProposals",
    "Provenance",
    "Scope",
    "SelfProfile",
    "SummaryMeta",
]
