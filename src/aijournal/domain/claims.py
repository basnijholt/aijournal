"""Strict claim/domain models shared across persona and profile pipelines."""

from __future__ import annotations

from pydantic import Field, field_validator

from aijournal.common.base import StrictModel
from aijournal.domain.enums import ClaimMethod, ClaimStatus, ClaimType
from aijournal.domain.evidence import SourceRef, Span

# Backwards-compatibility aliases for callers that still reference legacy names.
ClaimSourceSpan = Span
ClaimSource = SourceRef


class Scope(StrictModel):
    """Contextual qualifiers for a claim atom."""

    domain: str | None = None
    context: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)


class Provenance(StrictModel):
    """Provenance metadata recorded for a claim atom."""

    sources: list[ClaimSource] = Field(default_factory=list)
    first_seen: str | None = None
    last_updated: str
    observation_count: int = Field(default=1, ge=1)


class ClaimAtom(StrictModel):
    """Typed, scoped claim describing part of the persona."""

    id: str
    type: ClaimType
    subject: str
    predicate: str
    value: str
    statement: str
    scope: Scope = Field(default_factory=Scope)
    strength: float = Field(default=0.5, ge=0.0, le=1.0)
    status: ClaimStatus = ClaimStatus.TENTATIVE
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


class ClaimAtomsFile(StrictModel):
    """Container persisted on disk for multiple claim atoms."""

    claims: list[ClaimAtom] = Field(default_factory=list)
