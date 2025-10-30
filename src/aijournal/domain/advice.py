"""Strict advice card models shared by CLI and services."""

from __future__ import annotations

from pydantic import Field

from aijournal.common.base import StrictModel


class AdviceReference(StrictModel):
    """References included to ground why advice fits."""

    facets: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)


class AdviceRecommendation(StrictModel):
    """Single recommendation within an advice card."""

    title: str
    why_this_fits_you: AdviceReference = Field(default_factory=AdviceReference)
    steps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    mitigations: list[str] = Field(default_factory=list)


class AdviceCard(StrictModel):
    """Structured advice payload produced by LLM pipelines."""

    id: str | None = None
    query: str
    assumptions: list[str] = Field(default_factory=list)
    recommendations: list[AdviceRecommendation] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    confidence: float | None = None
    alignment: AdviceReference = Field(default_factory=AdviceReference)
    style: dict[str, object] = Field(default_factory=dict)
