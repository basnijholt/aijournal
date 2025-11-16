"""Domain-level models describing claim and facet change proposals."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from aijournal.common.base import StrictModel
from aijournal.domain.claims import Scope  # noqa: TC001
from aijournal.domain.enums import ClaimMethod, ClaimStatus, ClaimType, FacetOperation
from aijournal.domain.evidence import SourceRef  # noqa: TC001


class ClaimAtomInput(StrictModel):
    """Normalized claim payload without identifiers or provenance."""

    type: ClaimType
    subject: str
    predicate: str
    value: str
    statement: str
    scope: Scope
    strength: float
    status: ClaimStatus
    method: ClaimMethod
    user_verified: bool
    review_after_days: int

    @field_validator("strength")
    @classmethod
    def _check_strength(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            msg = "strength must be in [0,1]"
            raise ValueError(msg)
        return value


class ClaimProposal(StrictModel):
    """Structured claim update prepared for downstream review."""

    # Claim fields
    type: ClaimType
    subject: str
    predicate: str
    value: str
    statement: str
    scope: Scope
    strength: float
    status: ClaimStatus
    method: ClaimMethod
    user_verified: bool
    review_after_days: int
    # Proposal metadata
    normalized_ids: list[str] = Field(default_factory=list)
    evidence: list[SourceRef] = Field(default_factory=list)
    manifest_hashes: list[str] = Field(default_factory=list)
    rationale: str | None = None

    @field_validator("strength")
    @classmethod
    def _check_strength(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            msg = "strength must be in [0,1]"
            raise ValueError(msg)
        return value


class FacetChange(StrictModel):
    """Facet modification proposed by characterization pipelines."""

    path: str
    operation: FacetOperation
    value: Any | None = None
    method: str | None = None
    confidence: float | None = None
    review_after_days: int | None = None
    user_verified: bool | None = None
    evidence: list[SourceRef] = Field(default_factory=list)
    rationale: str | None = None

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: Any | None, info: Any) -> Any | None:
        operation = info.data.get("operation")
        if operation in {FacetOperation.SET, FacetOperation.MERGE} and value is None:
            msg = "value required for set/merge operations"
            raise ValueError(msg)
        return value


class ProfileUpdateProposals(StrictModel):
    """Aggregate container for proposed claim and facet updates."""

    claims: list[ClaimProposal] = Field(default_factory=list)
    facets: list[FacetChange] = Field(default_factory=list)
    interview_prompts: list[str] = Field(default_factory=list)
