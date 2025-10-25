"""Dataclasses describing aijournal's authoritative and derived artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class JournalEntry:
    """Human-authored Markdown entry metadata (PLAN §3.1)."""

    id: str
    created_at: str
    title: str
    tags: List[str] = field(default_factory=list)
    mood: Optional[str] = None
    projects: List[str] = field(default_factory=list)
    summary: Optional[str] = None


@dataclass(slots=True)
class JournalSection:
    """Heading/section metadata extracted from Markdown."""

    heading: str
    level: int = 1
    summary: Optional[str] = None
    para_index: Optional[int] = None


@dataclass(slots=True)
class NormalizedEntity:
    """Structured entity extracted during normalization."""

    type: str
    value: str
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedEntry:
    """Machine-readable normalized entry (PLAN §3.2)."""

    id: str
    created_at: str
    source_path: str
    title: str
    tags: List[str] = field(default_factory=list)
    sections: List[JournalSection] = field(default_factory=list)
    entities: List[NormalizedEntity] = field(default_factory=list)
    summary: Optional[str] = None
    source_hash: Optional[str] = None
    source_type: Optional[str] = None


@dataclass(slots=True)
class SummaryMeta:
    llm_model: str
    prompt_path: str
    created_at: str
    prompt_hash: Optional[str] = None


@dataclass(slots=True)
class DailySummary:
    """Derived day summary (PLAN §4.1)."""

    day: str
    bullets: List[str] = field(default_factory=list)
    highlights: List[str] = field(default_factory=list)
    todo_candidates: List[str] = field(default_factory=list)
    meta: SummaryMeta = field(default_factory=lambda: SummaryMeta(
        llm_model="unknown", prompt_path="", prompt_hash=None, created_at=""
    ))


@dataclass(slots=True)
class FactEvidenceSpan:
    type: str
    index: Optional[int] = None
    start: Optional[int] = None
    end: Optional[int] = None
    text: Optional[str] = None


@dataclass(slots=True)
class FactEvidence:
    entry_id: str
    spans: List[FactEvidenceSpan] = field(default_factory=list)


@dataclass(slots=True)
class MicroFact:
    id: str
    statement: str
    confidence: float
    evidence: FactEvidence
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None


@dataclass(slots=True)
class MicroFactsFile:
    facts: List[MicroFact] = field(default_factory=list)
    meta: SummaryMeta = field(default_factory=lambda: SummaryMeta(
        llm_model="unknown", prompt_path="", prompt_hash=None, created_at=""
    ))


@dataclass(slots=True)
class ClaimSourceSpan:
    type: str
    index: Optional[int] = None
    start: Optional[int] = None
    end: Optional[int] = None


@dataclass(slots=True)
class ClaimSource:
    entry_id: str
    spans: List[ClaimSourceSpan] = field(default_factory=list)


@dataclass(slots=True)
class Claim:
    id: str
    statement: str
    status: str
    confidence: float
    freshness: Optional[float] = None
    sources: List[ClaimSource] = field(default_factory=list)
    method: Optional[str] = None
    user_verified: bool = False
    review_after_days: Optional[int] = None
    last_updated: Optional[str] = None


@dataclass(slots=True)
class ClaimsFile:
    claims: List[Claim] = field(default_factory=list)


@dataclass(slots=True)
class SelfProfile:
    traits: Dict[str, Any] = field(default_factory=dict)
    values_motivations: Dict[str, Any] = field(default_factory=dict)
    goals: Dict[str, Any] = field(default_factory=dict)
    decision_style: Dict[str, Any] = field(default_factory=dict)
    affect_energy: Dict[str, Any] = field(default_factory=dict)
    social: Dict[str, Any] = field(default_factory=dict)
    boundaries_ethics: Dict[str, Any] = field(default_factory=dict)
    coaching_prefs: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProfileSuggestionUpsert:
    target: str
    operation: str
    value: Dict[str, Any]
    rationale: Optional[str] = None


@dataclass(slots=True)
class ProfileSuggestionUpdate:
    target: str
    operation: str
    value: Any
    method: Optional[str] = None
    user_verified: Optional[bool] = None
    evidence: Optional[List[str]] = None
    rationale: Optional[str] = None


@dataclass(slots=True)
class ProfileSuggestions:
    upserts: List[ProfileSuggestionUpsert] = field(default_factory=list)
    updates: List[ProfileSuggestionUpdate] = field(default_factory=list)
    meta: Optional[SummaryMeta] = None


@dataclass(slots=True)
class InterviewQuestion:
    id: str
    text: str
    target_facet: Optional[str] = None
    priority: Optional[str] = None


@dataclass(slots=True)
class InterviewSet:
    questions: List[InterviewQuestion] = field(default_factory=list)
    meta: Optional[SummaryMeta] = None


@dataclass(slots=True)
class AdviceReference:
    facets: List[str] = field(default_factory=list)
    claims: List[str] = field(default_factory=list)


@dataclass(slots=True)
class AdviceRecommendation:
    title: str
    why_this_fits_you: AdviceReference = field(default_factory=AdviceReference)
    steps: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    mitigations: List[str] = field(default_factory=list)


@dataclass(slots=True)
class AdviceCard:
    id: str
    query: str
    assumptions: List[str] = field(default_factory=list)
    recommendations: List[AdviceRecommendation] = field(default_factory=list)
    tradeoffs: List[str] = field(default_factory=list)
    next_actions: List[str] = field(default_factory=list)
    confidence: Optional[float] = None
    alignment: AdviceReference = field(default_factory=AdviceReference)
    style: Dict[str, Any] = field(default_factory=dict)
    meta: SummaryMeta = field(default_factory=lambda: SummaryMeta(
        llm_model="unknown", prompt_path="", prompt_hash=None, created_at=""
    ))


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
