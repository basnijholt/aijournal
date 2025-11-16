"""Derived data models for aijournal."""

from __future__ import annotations

from pydantic import Field

from aijournal.domain.advice import AdviceCard as _AdviceCard
from aijournal.domain.advice import AdviceRecommendation as _AdviceRecommendation
from aijournal.domain.advice import AdviceReference as _AdviceReference
from aijournal.domain.changes import ProfileUpdateProposals
from aijournal.domain.claims import ClaimAtom
from aijournal.domain.events import ClaimPreviewEvent  # noqa: TC001
from aijournal.domain.facts import MicroFactsFile
from aijournal.domain.persona import InterviewQuestion, InterviewSet, PersonaCore

from .base import AijournalModel

PersonaCore.model_rebuild(_types_namespace={"ClaimAtom": ClaimAtom})
InterviewSet.model_rebuild(
    _types_namespace={
        "InterviewQuestion": InterviewQuestion,
    },
)


AdviceReference = _AdviceReference
AdviceRecommendation = _AdviceRecommendation
AdviceCard = _AdviceCard


class ProfileUpdatePreview(AijournalModel):
    """Preview metadata bundled with a profile update batch."""

    claim_events: list[ClaimPreviewEvent] = Field(default_factory=list)
    interview_prompts: list[str] = Field(default_factory=list)


class ProfileUpdateInput(AijournalModel):
    """Normalized entry metadata captured in a characterization batch."""

    id: str
    normalized_path: str
    source_hash: str | None = None
    manifest_hash: str | None = None
    tags: list[str] = Field(default_factory=list)


class ProfileUpdateBatch(AijournalModel):
    """Pending profile update batch emitted by the unified profile update stage/CLI."""

    batch_id: str
    created_at: str
    date: str
    inputs: list[ProfileUpdateInput] = Field(default_factory=list)
    proposals: ProfileUpdateProposals = Field(default_factory=ProfileUpdateProposals)
    preview: ProfileUpdatePreview | None = None


MicroFactsFile.model_rebuild(_types_namespace={"ProfileUpdatePreview": ProfileUpdatePreview})
