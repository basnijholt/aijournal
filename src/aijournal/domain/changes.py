"""Domain-level models describing claim and facet change proposals."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from aijournal.common.base import StrictModel
from aijournal.domain.claims import Scope
from aijournal.domain.enums import ClaimMethod, ClaimStatus, ClaimType, FacetOperation
from aijournal.domain.evidence import SourceRef, Span


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _enforce_word_limit(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    words = value.split()
    if len(words) > limit:
        raise ValueError(f"reason must be ≤{limit} words (got {len(words)})")
    return value


class ClaimProposal(StrictModel):
    """Flattened claim proposal emitted by the LLM."""

    type: ClaimType
    statement: str = Field(..., max_length=160)
    subject: str = Field(default="self", max_length=80)
    predicate: str = Field(default="states", max_length=80)
    value: str | None = Field(default=None, max_length=160)
    reason: str | None = None
    evidence_entry: str | None = None
    evidence_para: int = Field(default=0, ge=0)

    scope: Scope = Field(default_factory=Scope, exclude=True)
    strength: float = Field(default=0.55, ge=0.0, le=1.0, exclude=True)
    status: ClaimStatus = Field(default=ClaimStatus.TENTATIVE, exclude=True)
    method: ClaimMethod = Field(default=ClaimMethod.INFERRED, exclude=True)
    user_verified: bool = Field(default=False, exclude=True)
    review_after_days: int = Field(default=120, exclude=True)
    evidence: list[SourceRef] = Field(default_factory=list, exclude=True)
    normalized_ids: list[str] = Field(default_factory=list, exclude=True)
    manifest_hashes: list[str] = Field(default_factory=list, exclude=True)

    @field_validator("statement", "subject", "predicate", mode="before")
    @classmethod
    def _strip_required(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
        if not value:
            raise ValueError("field cannot be empty")
        return value

    @field_validator("value", mode="after")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        return _clean_text(value)

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str | None) -> str | None:
        value = _clean_text(value)
        return _enforce_word_limit(value, limit=25)

    @model_validator(mode="after")
    def _apply_defaults(self) -> ClaimProposal:
        if not self.value:
            object.__setattr__(self, "value", self.statement)
        if self.evidence_entry and not self.evidence:
            span = Span(type="para", index=self.evidence_para)
            source = SourceRef(entry_id=self.evidence_entry, spans=[span])
            object.__setattr__(self, "evidence", [source])
        return self

    def claim_fields(self) -> dict[str, Any]:
        """Return a dict suitable for ClaimAtom normalization."""

        return {
            "type": self.type,
            "subject": self.subject,
            "predicate": self.predicate,
            "value": self.value or self.statement,
            "statement": self.statement,
            "scope": self.scope,
            "strength": self.strength,
            "status": self.status,
            "method": self.method,
            "user_verified": self.user_verified,
            "review_after_days": self.review_after_days,
        }


class FacetChange(StrictModel):
    """Facet modification proposed by characterization pipelines."""

    path: str
    action: FacetOperation = Field(default=FacetOperation.SET)
    value: Any | None = None
    reason: str | None = None
    evidence_entry: str | None = None
    evidence_para: int = Field(default=0, ge=0)

    method: str = Field(default="inferred", exclude=True)
    confidence: float = Field(default=0.55, exclude=True)
    review_after_days: int = Field(default=120, exclude=True)
    user_verified: bool = Field(default=False, exclude=True)
    evidence: list[SourceRef] = Field(default_factory=list, exclude=True)

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("path cannot be empty")
        return text

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str | None) -> str | None:
        value = _clean_text(value)
        return _enforce_word_limit(value, limit=25)

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: Any | None, info: Any) -> Any | None:
        action = info.data.get("action")
        if action == FacetOperation.SET and value is None:
            raise ValueError("value required for set operations")
        return value

    @model_validator(mode="after")
    def _populate_defaults(self) -> FacetChange:
        if self.evidence_entry and not self.evidence:
            span = Span(type="para", index=self.evidence_para)
            source = SourceRef(entry_id=self.evidence_entry, spans=[span])
            object.__setattr__(self, "evidence", [source])
        return self


class ProfileUpdateProposals(StrictModel):
    """Aggregate container for proposed claim and facet updates."""

    claims: list[ClaimProposal] = Field(default_factory=list)
    facets: list[FacetChange] = Field(default_factory=list)
    interview_prompts: list[str] = Field(default_factory=list)
