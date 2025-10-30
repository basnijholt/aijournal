"""Pydantic models describing aijournal's authoritative and derived artifacts."""

from __future__ import annotations

from aijournal.domain.changes import ClaimProposal, FacetChange, ProfileUpdateProposals
from aijournal.domain.events import (
    ClaimConflictPayload,
    ClaimPreviewEvent,
    ClaimSignaturePayload,
    FeedbackAdjustmentEvent,
    FeedbackBatch,
)
from aijournal.domain.facts import (
    DailySummary,
    FactEvidence,
    FactEvidenceSpan,
    MicroFact,
    MicroFactsFile,
    SummaryMeta,
)
from aijournal.domain.index import IndexMeta
from aijournal.domain.journal import NormalizedEntity, NormalizedEntry
from aijournal.domain.persona import (
    InterviewQuestion,
    InterviewSet,
    PersonaCore,
    PersonaCoreFile,
    PersonaCoreMeta,
)

from .authoritative import (
    ClaimsFile,
    JournalEntry,
    JournalSection,
    JsonScalar,
    JsonValue,
    ManifestEntry,
    SelfProfile,
)
from .base import AijournalModel
from .claim_atoms import (
    ClaimAtom,
    ClaimAtomsFile,
    ClaimMethod,
    ClaimSource,
    ClaimSourceSpan,
    ClaimStatus,
    ClaimType,
    Provenance,
    Scope,
)
from .derived import (
    AdviceCard,
    AdviceRecommendation,
    AdviceReference,
    ChunkManifest,
    ChunkManifestChunk,
    ChunkManifestMeta,
    ProfileSuggestions,
    ProfileSuggestionUpdate,
    ProfileSuggestionUpsert,
    ProfileUpdateBatch,
    ProfileUpdateInput,
    ProfileUpdatePreview,
)
from .responses import AdviceLLMResponse

Claim = ClaimAtom

__all__ = [
    "AdviceCard",
    "AdviceLLMResponse",
    "AdviceRecommendation",
    "AdviceReference",
    "AijournalModel",
    "Claim",
    "ClaimAtom",
    "ClaimAtomsFile",
    "ClaimConflictPayload",
    "ClaimMethod",
    "ClaimPreviewEvent",
    "ClaimProposal",
    "ClaimSignaturePayload",
    "ClaimSource",
    "ClaimSourceSpan",
    "ClaimStatus",
    "ClaimType",
    "ChunkManifest",
    "ChunkManifestChunk",
    "ChunkManifestMeta",
    "ClaimsFile",
    "DailySummary",
    "FeedbackAdjustmentEvent",
    "FeedbackBatch",
    "FactEvidence",
    "FactEvidenceSpan",
    "FacetChange",
    "IndexMeta",
    "InterviewQuestion",
    "InterviewSet",
    "JournalEntry",
    "JournalSection",
    "JsonScalar",
    "JsonValue",
    "ManifestEntry",
    "MicroFact",
    "MicroFactsFile",
    "NormalizedEntry",
    "NormalizedEntity",
    "PersonaCore",
    "PersonaCoreFile",
    "PersonaCoreMeta",
    "ProfileSuggestionUpsert",
    "ProfileSuggestionUpdate",
    "ProfileSuggestions",
    "ProfileUpdateBatch",
    "ProfileUpdateInput",
    "ProfileUpdatePreview",
    "ProfileUpdateProposals",
    "Provenance",
    "Scope",
    "SelfProfile",
    "SummaryMeta",
]
