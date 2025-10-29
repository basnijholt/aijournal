"""Typed claim atom models with scope and provenance."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from aijournal.domain.evidence import SourceRef, Span

from .base import AijournalModel

ClaimType = Literal[
    "preference",
    "value",
    "goal",
    "boundary",
    "trait",
    "habit",
    "aversion",
    "skill",
]
ClaimStatus = Literal["accepted", "tentative", "rejected"]
ClaimMethod = Literal["self_report", "inferred", "behavioral"]


ClaimSourceSpan = Span
ClaimSource = SourceRef


class Scope(AijournalModel):
    """Contextual qualifiers for a claim atom."""

    domain: str | None = None
    context: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)


class Provenance(AijournalModel):
    """Provenance metadata for a claim atom."""

    sources: list[ClaimSource] = Field(default_factory=list)
    first_seen: str | None = None
    last_updated: str
    observation_count: int = Field(default=1, ge=1)


class ClaimAtom(AijournalModel):
    """Typed, scoped claim describing the persona."""

    id: str
    type: ClaimType
    subject: str
    predicate: str
    value: str
    statement: str
    scope: Scope = Field(default_factory=Scope)
    strength: float = Field(default=0.5, ge=0.0, le=1.0)
    status: ClaimStatus = "tentative"
    method: ClaimMethod
    user_verified: bool = False
    review_after_days: int = 120
    provenance: Provenance

    @field_validator("provenance")
    @classmethod
    def _ensure_redacted(cls, provenance: Provenance) -> Provenance:
        for source in provenance.sources:
            for span in source.spans:
                if span.text is not None:
                    msg = "claim provenance spans must not carry raw text"
                    raise ValueError(msg)
        return provenance


class ClaimAtomsFile(AijournalModel):
    """Container for multiple claim atoms."""

    claims: list[ClaimAtom] = Field(default_factory=list)


__all__ = [
    "ClaimAtom",
    "ClaimAtomsFile",
    "ClaimMethod",
    "ClaimSource",
    "ClaimSourceSpan",
    "ClaimStatus",
    "ClaimType",
    "Provenance",
    "Scope",
]
