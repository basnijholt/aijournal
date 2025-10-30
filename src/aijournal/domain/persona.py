"""Persona and interview domain models for strict schema alignment."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field

from aijournal.common.base import StrictModel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aijournal.domain.facts import SummaryMeta
    from aijournal.models.claim_atoms import ClaimAtom


class PersonaCoreMeta(StrictModel):
    """Metadata captured alongside the persona core artifact."""

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


class PersonaCore(StrictModel):
    """Primary persona payload used by chat/advise pipelines."""

    profile: dict[str, Any] = Field(default_factory=dict)
    claims: list[ClaimAtom] = Field(default_factory=list)


class PersonaCoreFile(StrictModel):
    """Persona core artifact envelope stored on disk."""

    persona: PersonaCore = Field(default_factory=PersonaCore)
    meta: PersonaCoreMeta


class InterviewQuestion(StrictModel):
    """Structured interview question proposed by the characterization pipeline."""

    id: str
    text: str
    target_facet: str | None = None
    priority: str | None = None


class InterviewSet(StrictModel):
    """Collection of interview questions to review with the operator."""

    questions: list[InterviewQuestion] = Field(default_factory=list)
    meta: SummaryMeta | None = None


__all__ = [
    "InterviewQuestion",
    "InterviewSet",
    "PersonaCore",
    "PersonaCoreFile",
    "PersonaCoreMeta",
]
