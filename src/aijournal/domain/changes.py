"""Domain-level models describing claim and facet change proposals."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from aijournal.common.base import StrictModel
from aijournal.domain.evidence import SourceRef
from aijournal.models.claim_atoms import Scope


class ClaimAtomInput(StrictModel):
    """Normalized claim payload without identifiers or provenance."""

    type: Literal[
        "preference",
        "value",
        "goal",
        "boundary",
        "trait",
        "habit",
        "aversion",
        "skill",
    ]
    subject: str
    predicate: str
    value: str
    statement: str
    scope: Scope
    strength: float
    status: Literal["accepted", "tentative", "rejected"]
    method: Literal["self_report", "inferred", "behavioral"]
    user_verified: bool
    review_after_days: int

    @field_validator("strength")
    @classmethod
    def _check_strength(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("strength must be in [0,1]")
        return value


class ClaimProposal(StrictModel):
    """Structured claim update prepared for downstream review."""

    claim: ClaimAtomInput
    normalized_ids: list[str] = Field(default_factory=list)
    evidence: list[SourceRef] = Field(default_factory=list)
    manifest_hashes: list[str] = Field(default_factory=list)
    rationale: str | None = None


class FacetChange(StrictModel):
    """Facet modification proposed by characterization pipelines."""

    path: str
    operation: Literal["set", "remove", "merge"]
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
        if operation in {"set", "merge"} and value is None:
            raise ValueError("value required for set/merge operations")
        return value


class ProfileUpdateProposals(StrictModel):
    """Aggregate container for proposed claim and facet updates."""

    claims: list[ClaimProposal] = Field(default_factory=list)
    facets: list[FacetChange] = Field(default_factory=list)
    interview_prompts: list[str] = Field(default_factory=list)


__all__ = [
    "ClaimAtomInput",
    "ClaimProposal",
    "FacetChange",
    "ProfileUpdateProposals",
]
