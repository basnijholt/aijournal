"""Pydantic models describing aijournal's authoritative and derived artifacts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class AijournalModel(BaseModel):
    """Base model with sensible defaults for YAML serialization."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )


class JournalEntry(AijournalModel):
    """Human-authored Markdown entry metadata (PLAN §3.1)."""

    id: str
    created_at: str
    title: str
    tags: List[str] = Field(default_factory=list)
    mood: Optional[str] = None
    projects: List[str] = Field(default_factory=list)
    summary: Optional[str] = None


class JournalSection(AijournalModel):
    """Heading/section metadata extracted from Markdown."""

    heading: str
    level: int = 1
    summary: Optional[str] = None
    para_index: Optional[int] = None


class NormalizedEntity(AijournalModel):
    """Structured entity extracted during normalization."""

    type: str
    value: str
    extra: Dict[str, Any] = Field(default_factory=dict)


class NormalizedEntry(AijournalModel):
    """Machine-readable normalized entry (PLAN §3.2)."""

    id: str
    created_at: str
    source_path: str
    title: str
    tags: List[str] = Field(default_factory=list)
    sections: List[JournalSection] = Field(default_factory=list)
    entities: List[NormalizedEntity] = Field(default_factory=list)
    summary: Optional[str] = None
    source_hash: Optional[str] = None
    source_type: Optional[str] = None


class SummaryMeta(AijournalModel):
    llm_model: str = "unknown"
    prompt_path: str = ""
    prompt_hash: Optional[str] = None
    created_at: str = ""


class DailySummary(AijournalModel):
    """Derived day summary (PLAN §4.1)."""

    day: str
    bullets: List[str] = Field(default_factory=list)
    highlights: List[str] = Field(default_factory=list)
    todo_candidates: List[str] = Field(default_factory=list)
    meta: SummaryMeta = Field(default_factory=SummaryMeta)


class FactEvidenceSpan(AijournalModel):
    type: str
    index: Optional[int] = None
    start: Optional[int] = None
    end: Optional[int] = None
    text: Optional[str] = None


class FactEvidence(AijournalModel):
    entry_id: str
    spans: List[FactEvidenceSpan] = Field(default_factory=list)


class MicroFact(AijournalModel):
    id: str
    statement: str
    confidence: float
    evidence: FactEvidence
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None


class MicroFactsFile(AijournalModel):
    facts: List[MicroFact] = Field(default_factory=list)
    meta: SummaryMeta = Field(default_factory=SummaryMeta)


class ClaimSourceSpan(AijournalModel):
    type: str
    index: Optional[int] = None
    start: Optional[int] = None
    end: Optional[int] = None


class ClaimSource(AijournalModel):
    entry_id: str
    spans: List[ClaimSourceSpan] = Field(default_factory=list)


class Claim(AijournalModel):
    id: str
    statement: str
    status: str
    confidence: float
    freshness: Optional[float] = None
    sources: List[ClaimSource] = Field(default_factory=list)
    method: Optional[str] = None
    user_verified: bool = False
    review_after_days: Optional[int] = None
    last_updated: Optional[str] = None
    evidence: Optional[List[str]] = None


class ClaimsFile(AijournalModel):
    claims: List[Claim] = Field(default_factory=list)


class SelfProfile(AijournalModel):
    traits: Dict[str, Any] = Field(default_factory=dict)
    values_motivations: Dict[str, Any] = Field(default_factory=dict)
    goals: Dict[str, Any] = Field(default_factory=dict)
    decision_style: Dict[str, Any] = Field(default_factory=dict)
    affect_energy: Dict[str, Any] = Field(default_factory=dict)
    social: Dict[str, Any] = Field(default_factory=dict)
    boundaries_ethics: Dict[str, Any] = Field(default_factory=dict)
    coaching_prefs: Dict[str, Any] = Field(default_factory=dict)


class ProfileSuggestionUpsert(AijournalModel):
    target: str
    operation: str
    value: Dict[str, Any]
    rationale: Optional[str] = None


class ProfileSuggestionUpdate(AijournalModel):
    target: str
    operation: str
    value: Any
    method: Optional[str] = None
    user_verified: Optional[bool] = None
    evidence: Optional[List[str]] = None
    rationale: Optional[str] = None


class ProfileSuggestions(AijournalModel):
    upserts: List[ProfileSuggestionUpsert] = Field(default_factory=list)
    updates: List[ProfileSuggestionUpdate] = Field(default_factory=list)
    meta: Optional[SummaryMeta] = None


class InterviewQuestion(AijournalModel):
    id: str
    text: str
    target_facet: Optional[str] = None
    priority: Optional[str] = None


class InterviewSet(AijournalModel):
    questions: List[InterviewQuestion] = Field(default_factory=list)
    meta: Optional[SummaryMeta] = None


class AdviceReference(AijournalModel):
    facets: List[str] = Field(default_factory=list)
    claims: List[str] = Field(default_factory=list)


class AdviceRecommendation(AijournalModel):
    title: str
    why_this_fits_you: AdviceReference = Field(default_factory=AdviceReference)
    steps: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    mitigations: List[str] = Field(default_factory=list)


class AdviceCard(AijournalModel):
    id: str
    query: str
    assumptions: List[str] = Field(default_factory=list)
    recommendations: List[AdviceRecommendation] = Field(default_factory=list)
    tradeoffs: List[str] = Field(default_factory=list)
    next_actions: List[str] = Field(default_factory=list)
    confidence: Optional[float] = None
    alignment: AdviceReference = Field(default_factory=AdviceReference)
    style: Dict[str, Any] = Field(default_factory=dict)
    meta: SummaryMeta = Field(default_factory=SummaryMeta)


__all__ = [
    "AdviceCard",
    "AdviceRecommendation",
    "AdviceReference",
    "Claim",
    "ClaimSource",
    "ClaimSourceSpan",
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
    "ProfileSuggestionUpdate",
    "ProfileSuggestionUpsert",
    "ProfileSuggestions",
    "SelfProfile",
    "SummaryMeta",
]
