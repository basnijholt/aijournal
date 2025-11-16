"""Strict claim/domain models shared across persona and profile pipelines."""

from __future__ import annotations

from pydantic import Field

from aijournal.common.base import StrictModel
from aijournal.domain.enums import ClaimStatus, ClaimType
from aijournal.domain.evidence import SourceRef

# Type alias for claim evidence sources.
ClaimSource = SourceRef


class Scope(StrictModel):
    """Contextual qualifiers for a claim atom."""

    domain: str | None = None
    context: list[str] = Field(default_factory=list)


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
    statement: str
    scope: Scope = Field(default_factory=Scope)
    strength: float = Field(default=0.5, ge=0.0, le=1.0)
    status: ClaimStatus = ClaimStatus.TENTATIVE
    review_after_days: int = 120
    provenance: Provenance


class ClaimAtomsFile(StrictModel):
    """Container persisted on disk for multiple claim atoms."""

    claims: list[ClaimAtom] = Field(default_factory=list)
