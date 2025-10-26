"""Pydantic models describing aijournal's authoritative and derived artifacts."""

from __future__ import annotations

from typing import Any, cast

from pydantic import ConfigDict, Field, field_validator

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


class ManifestEntry(AijournalModel):
    """Manifest row describing an ingested Markdown source."""

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    hash: str
    path: str
    normalized: str
    source_type: str | None = None
    ingested_at: str
    created_at: str
    id: str
    tags: list[str] = Field(default_factory=list)
    model: str | None = None


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
    claim_proposals: list[ClaimProposal] = Field(default_factory=list)
    preview: ProfileUpdatePreview | None = None
    meta: SummaryMeta = Field(default_factory=SummaryMeta)


class ChunkManifestMeta(AijournalModel):
    embedding_model: str
    vector_dimension: int
    generated_at: str


class ChunkManifestChunk(AijournalModel):
    chunk_id: str
    normalized_id: str
    chunk_index: int
    chunk_text: str
    tags: list[str] = Field(default_factory=list)
    source_type: str | None = None
    source_path: str
    tokens: int = 0
    source_hash: str | None = None
    manifest_hash: str | None = None


class ChunkManifest(AijournalModel):
    day: str
    chunks: list[ChunkManifestChunk] = Field(default_factory=list)
    meta: ChunkManifestMeta


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
    value: ClaimAtom
    rationale: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value(cls, value: Any) -> ClaimAtom:
        if isinstance(value, ClaimAtom):
            return value
        if not isinstance(value, dict):
            raise TypeError("Profile suggestion value must be a ClaimAtom or mapping.")

        data = cast(dict[str, Any], value)

        statement = str(data.get("statement") or "").strip()
        if not statement:
            raise ValueError("Claim statement is required")

        claim_id = str(data.get("id") or "suggested-claim")
        claim_type = str(data.get("type") or "preference").strip().lower() or "preference"
        allowed_types = {
            "preference",
            "value",
            "goal",
            "boundary",
            "trait",
            "habit",
            "aversion",
            "skill",
        }
        if claim_type not in allowed_types:
            claim_type = "preference"

        predicate = str(data.get("predicate") or "statement")
        subject = str(data.get("subject") or claim_id or statement)

        normalized_value = data.get("value")
        if normalized_value is None or not str(normalized_value).strip():
            normalized_value = statement

        strength_raw = data.get("strength", data.get("confidence"))
        try:
            strength = float(strength_raw) if strength_raw is not None else 0.6
        except (TypeError, ValueError):
            strength = 0.6
        strength = max(0.0, min(1.0, strength))

        status = str(data.get("status") or "tentative").strip().lower()
        if status not in {"accepted", "tentative", "rejected"}:
            status = "tentative"

        method = str(data.get("method") or "inferred").strip().lower()
        if method not in {"self_report", "inferred", "behavioral"}:
            method = "inferred"

        review_after_raw = data.get("review_after_days")
        try:
            review_after_days = int(review_after_raw) if review_after_raw is not None else 120
        except (TypeError, ValueError):
            review_after_days = 120

        scope_raw = data.get("scope")
        scope_data = cast(dict[str, Any], scope_raw) if isinstance(scope_raw, dict) else {}
        scope = Scope.model_validate(scope_data) if scope_data else Scope()

        provenance_raw = data.get("provenance")
        provenance_data = (
            cast(dict[str, Any], provenance_raw) if isinstance(provenance_raw, dict) else {}
        )
        sources_raw = provenance_data.get("sources")
        sources: list[ClaimSource] = []
        if isinstance(sources_raw, list):
            for source in sources_raw:
                if not isinstance(source, dict):
                    continue
                source_dict = cast(dict[str, Any], source)
                entry_id = source_dict.get("entry_id")
                if not entry_id:
                    continue
                spans_raw = source_dict.get("spans")
                spans: list[ClaimSourceSpan] = []
                if isinstance(spans_raw, list):
                    for span in spans_raw:
                        if not isinstance(span, dict):
                            continue
                        span_dict = cast(dict[str, Any], span)
                        spans.append(
                            ClaimSourceSpan(
                                type=str(span_dict.get("type") or "excerpt"),
                                index=span_dict.get("index"),
                                start=span_dict.get("start"),
                                end=span_dict.get("end"),
                            ),
                        )
                sources.append(ClaimSource(entry_id=str(entry_id), spans=spans))

        observation_count_raw = provenance_data.get("observation_count")
        try:
            observation_count = (
                int(observation_count_raw) if observation_count_raw is not None else 1
            )
        except (TypeError, ValueError):
            observation_count = 1
        if observation_count <= 0:
            observation_count = max(1, len(sources) or 1)

        first_seen_raw = provenance_data.get("first_seen")
        first_seen = str(first_seen_raw) if first_seen_raw is not None else None
        last_updated_raw = provenance_data.get("last_updated")
        last_updated = (
            str(last_updated_raw) if last_updated_raw is not None else "1970-01-01T00:00:00Z"
        )

        provenance = Provenance(
            sources=sources,
            first_seen=first_seen,
            last_updated=last_updated,
            observation_count=observation_count,
        )

        return ClaimAtom(
            id=claim_id[:96],
            type=claim_type,  # type: ignore[arg-type]
            subject=subject,
            predicate=predicate,
            value=str(normalized_value),
            statement=statement,
            scope=scope,
            strength=strength,
            status=status,  # type: ignore[arg-type]
            method=method,  # type: ignore[arg-type]
            user_verified=bool(data.get("user_verified", False)),
            review_after_days=review_after_days,
            provenance=provenance,
        )


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


class ClaimSignaturePayload(AijournalModel):
    """Serialized signature describing the target slot for a claim."""

    claim_type: str
    subject: str
    predicate: str
    domain: str | None = None
    context: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)


class ClaimConflictPayload(AijournalModel):
    """Structured conflict emitted during consolidation previews."""

    claim_id: str
    signature: ClaimSignaturePayload
    statement: str
    existing_value: str
    incoming_value: str
    incoming_sources: list[ClaimSource] = Field(default_factory=list)


class ClaimPreviewEvent(AijournalModel):
    """Outcome of attempting to merge a claim proposal into existing atoms."""

    action: str
    claim_id: str
    delta_strength: float = 0.0
    statement: str | None = None
    value: str | None = None
    strength: float | None = None
    signature: ClaimSignaturePayload | None = None
    conflict: ClaimConflictPayload | None = None
    related_claim_id: str | None = None
    related_action: str | None = None
    related_signature: ClaimSignaturePayload | None = None


class ProfileUpdatePreview(AijournalModel):
    """Preview metadata bundled with a profile update batch."""

    claim_events: list[ClaimPreviewEvent] = Field(default_factory=list)
    interview_prompts: list[str] = Field(default_factory=list)


class ProfileUpdateBatch(AijournalModel):
    """Pending profile update batch emitted by `aijournal characterize`."""

    batch_id: str
    created_at: str
    date: str
    inputs: list[ProfileUpdateInput] = Field(default_factory=list)
    proposals: ProfileUpdateProposals = Field(default_factory=ProfileUpdateProposals)
    meta: SummaryMeta = Field(default_factory=SummaryMeta)
    preview: ProfileUpdatePreview | None = None


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
    source_mtimes: dict[str, float] = Field(default_factory=dict)


class PersonaCore(AijournalModel):
    profile: dict[str, Any] = Field(default_factory=dict)
    claims: list[ClaimAtom] = Field(default_factory=list)


class PersonaCoreFile(AijournalModel):
    persona: PersonaCore = Field(default_factory=PersonaCore)
    meta: PersonaCoreMeta


class IndexMeta(AijournalModel):
    embedding_model: str | None = None
    vector_dimension: int | None = None
    chunk_count: int | None = None
    entry_count: int | None = None
    mode: str | None = None
    fake_mode: bool | None = None
    annoy_trees: int | None = None
    search_k_factor: float | None = None
    char_per_token: float | None = None
    since: str | None = None
    limit: int | None = None
    touched_dates: list[str] = Field(default_factory=list)
    updated_at: str | None = None


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
    "ClaimConflictPayload",
    "ClaimPreviewEvent",
    "ClaimSignaturePayload",
    "ClaimStatus",
    "ClaimType",
    "ChunkManifest",
    "ChunkManifestChunk",
    "ChunkManifestMeta",
    "ClaimsFile",
    "DailySummary",
    "FactEvidence",
    "FactEvidenceSpan",
    "InterviewQuestion",
    "InterviewSet",
    "ManifestEntry",
    "JournalEntry",
    "JournalSection",
    "MicroFact",
    "MicroFactsFile",
    "NormalizedEntity",
    "NormalizedEntry",
    "PersonaCore",
    "PersonaCoreFile",
    "PersonaCoreMeta",
    "IndexMeta",
    "ProfileSuggestionUpdate",
    "ProfileSuggestionUpsert",
    "ProfileSuggestions",
    "ProfileUpdateBatch",
    "ProfileUpdateInput",
    "ProfileUpdatePreview",
    "ProfileUpdateProposals",
    "Provenance",
    "Scope",
    "SelfProfile",
    "SummaryMeta",
]
