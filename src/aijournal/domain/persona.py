"""Persona and interview domain models for strict schema alignment."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from aijournal.common.base import StrictModel
from aijournal.domain.claims import ClaimAtom  # noqa: TC001


class PersonaCore(StrictModel):
    """Primary persona payload used by chat/advise pipelines."""

    profile: dict[str, Any] = Field(default_factory=dict)
    claims: list[ClaimAtom] = Field(default_factory=list)


class InterviewQuestion(StrictModel):
    """Structured interview question proposed by the characterization pipeline."""

    id: str
    text: str
    target_facet: str | None = None
    priority: str | None = None


class InterviewSet(StrictModel):
    """Collection of interview questions to review with the operator."""

    questions: list[InterviewQuestion] = Field(default_factory=list)
