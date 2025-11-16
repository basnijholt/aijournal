"""Domain models for extracted facts and daily summaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field

from aijournal.common.base import StrictModel
from aijournal.domain.changes import ClaimProposal  # noqa: TC001
from aijournal.domain.evidence import SourceRef, Span

if TYPE_CHECKING:  # pragma: no cover
    from aijournal.models.derived import ProfileUpdatePreview


class DailySummary(StrictModel):
    """Derived day summary (PLAN §4.1)."""

    day: str
    bullets: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    todo_candidates: list[str] = Field(default_factory=list)


FactEvidenceSpan = Span
FactEvidence = SourceRef


class MicroFact(StrictModel):
    id: str
    statement: str
    confidence: float
    evidence: FactEvidence
    first_seen: str | None = None
    last_seen: str | None = None


class MicroFactsFile(StrictModel):
    facts: list[MicroFact] = Field(default_factory=list)
    claim_proposals: list[ClaimProposal] = Field(default_factory=list)
    preview: ProfileUpdatePreview | None = None


class ConsolidatedMicroFact(StrictModel):
    """Global microfact entry that survives consolidation runs."""

    id: str
    statement: str
    canonical_statement: str
    confidence: float
    first_seen: str
    last_seen: str
    observation_count: int
    domain: str | None = None
    contexts: list[str] = Field(default_factory=list)
    evidence_entries: list[str] = Field(default_factory=list)
    source_fact_ids: list[str] = Field(default_factory=list)


class ConsolidatedMicrofactsFile(StrictModel):
    """Artifact capturing the global consolidated microfact snapshot."""

    generated_at: str
    embedding_model: str | None = None
    facts: list[ConsolidatedMicroFact] = Field(default_factory=list)


class MicrofactConsolidationSummary(StrictModel):
    """Per-day summary emitted during rebuild operations."""

    day: str
    processed: int
    new_records: int
    merged_records: int


class MicrofactConsolidationLog(StrictModel):
    """Artifact capturing the rebuild run summaries."""

    generated_at: str
    entries: list[MicrofactConsolidationSummary] = Field(default_factory=list)
