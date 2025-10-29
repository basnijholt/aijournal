"""Domain-level models shared across the project."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .journal import NormalizedEntity, NormalizedEntry, Section

if TYPE_CHECKING:  # pragma: no cover - for type checkers only
    from .changes import ClaimAtomInput, ClaimProposal, FacetChange, ProfileUpdateProposals
    from .evidence import SourceRef, Span, redact_source_text
    from .facts import (
        DailySummary,
        FactEvidence,
        FactEvidenceSpan,
        MicroFact,
        MicroFactsFile,
        SummaryMeta,
    )
    from .persona import (
        InterviewQuestion,
        InterviewSet,
        PersonaCore,
        PersonaCoreFile,
        PersonaCoreMeta,
    )

__all__ = [
    "ClaimAtomInput",
    "ClaimProposal",
    "FacetChange",
    "ProfileUpdateProposals",
    "SummaryMeta",
    "DailySummary",
    "MicroFact",
    "MicroFactsFile",
    "FactEvidence",
    "FactEvidenceSpan",
    "Section",
    "NormalizedEntity",
    "NormalizedEntry",
    "Span",
    "SourceRef",
    "redact_source_text",
    "InterviewQuestion",
    "InterviewSet",
    "PersonaCoreMeta",
    "PersonaCore",
    "PersonaCoreFile",
]

_CHANGES_EXPORTS = {
    "ClaimAtomInput",
    "ClaimProposal",
    "FacetChange",
    "ProfileUpdateProposals",
}
_EVIDENCE_EXPORTS = {"Span", "SourceRef", "redact_source_text"}
_FACTS_EXPORTS = {
    "SummaryMeta",
    "DailySummary",
    "MicroFact",
    "MicroFactsFile",
    "FactEvidence",
    "FactEvidenceSpan",
}
_PERSONA_EXPORTS = {
    "PersonaCoreMeta",
    "PersonaCore",
    "PersonaCoreFile",
    "InterviewQuestion",
    "InterviewSet",
}


def __getattr__(name: str) -> Any:
    if name in _CHANGES_EXPORTS:
        module = import_module("aijournal.domain.changes")
        value = getattr(module, name)
        globals()[name] = value
        return value
    if name in _EVIDENCE_EXPORTS:
        module = import_module("aijournal.domain.evidence")
        value = getattr(module, name)
        globals()[name] = value
        return value
    if name in _FACTS_EXPORTS:
        module = import_module("aijournal.domain.facts")
        value = getattr(module, name)
        globals()[name] = value
        return value
    if name in _PERSONA_EXPORTS:
        module = import_module("aijournal.domain.persona")
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(name)
