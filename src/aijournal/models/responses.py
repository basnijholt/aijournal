"""LLM-only response models that are not yet mirrored by domain schemas."""

from __future__ import annotations

from pydantic import Field

from .base import AijournalModel
from .derived import AdviceReference


class AdviceLLMRecommendation(AijournalModel):
    """Simplified recommendation payload emitted directly by the LLM."""

    title: str
    why_this_fits_you: AdviceReference = Field(default_factory=AdviceReference)
    steps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    mitigations: list[str] = Field(default_factory=list)


class AdviceLLMResponse(AijournalModel):
    """Minimal advice-card schema expected from the live LLM."""

    id: str
    query: str
    assumptions: list[str] = Field(default_factory=list)
    recommendations: list[AdviceLLMRecommendation] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    alignment: AdviceReference = Field(default_factory=AdviceReference)
    style: dict[str, object] = Field(default_factory=dict)


__all__ = [
    "AdviceLLMRecommendation",
    "AdviceLLMResponse",
]
