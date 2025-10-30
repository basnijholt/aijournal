"""Domain models for extracted facts and daily summaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field

from aijournal.common.base import StrictModel
from aijournal.domain.changes import ClaimProposal
from aijournal.domain.evidence import SourceRef, Span

if TYPE_CHECKING:  # pragma: no cover
    from aijournal.models.derived import ProfileUpdatePreview


class SummaryMeta(StrictModel):
    llm_model: str = "unknown"
    prompt_path: str = ""
    prompt_hash: str | None = None
    created_at: str = ""


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
