"""Authoritative data models for aijournal."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from .base import AijournalModel
from .claim_atoms import ClaimAtom

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list[Any] | dict[str, Any]


class ManifestEntry(AijournalModel):
    """Manifest row describing an ingested Markdown source."""

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    hash: str
    path: str
    normalized: str
    source_type: str | None = None
    ingested_at: str
    created_at: str
    id: str
    tags: list[str] = Field(default_factory=list)
    model: str | None = None
    canonical_journal_path: str | None = None
    snapshot_path: str | None = None
    aliases: list[str] = Field(default_factory=list)


class JournalEntry(AijournalModel):
    """Human-authored Markdown entry metadata."""

    id: str
    created_at: str
    title: str
    tags: list[str] = Field(default_factory=list)
    mood: str | None = None
    projects: list[str] = Field(default_factory=list)
    summary: str | None = None


class JournalSection(AijournalModel):
    """Heading/section metadata extracted from Markdown."""

    heading: str
    level: int = 1
    summary: str | None = None
    para_index: int | None = None


class NormalizedEntity(AijournalModel):
    """Structured entity extracted during normalization."""

    type: str
    value: str
    extra: dict[str, Any] = Field(default_factory=dict)


class NormalizedEntry(AijournalModel):
    """Machine-readable normalized entry."""

    id: str
    created_at: str
    source_path: str
    title: str
    tags: list[str] = Field(default_factory=list)
    sections: list[JournalSection] = Field(default_factory=list)
    entities: list[NormalizedEntity] = Field(default_factory=list)
    summary: str | None = None
    source_hash: str | None = None
    source_type: str | None = None


class ClaimsFile(AijournalModel):
    claims: list[ClaimAtom] = Field(default_factory=list)


class SelfProfile(AijournalModel):
    traits: dict[str, Any] = Field(default_factory=dict)
    values_motivations: dict[str, Any] = Field(default_factory=dict)
    goals: dict[str, Any] = Field(default_factory=dict)
    decision_style: dict[str, Any] = Field(default_factory=dict)
    affect_energy: dict[str, Any] = Field(default_factory=dict)
    planning: dict[str, Any] = Field(default_factory=dict)
    dashboard: dict[str, Any] = Field(default_factory=dict)
    habits: dict[str, Any] = Field(default_factory=dict)
    social: dict[str, Any] = Field(default_factory=dict)
    boundaries_ethics: dict[str, Any] = Field(default_factory=dict)
    coaching_prefs: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "JsonScalar",
    "JsonValue",
    "ManifestEntry",
    "JournalEntry",
    "JournalSection",
    "NormalizedEntity",
    "NormalizedEntry",
    "ClaimsFile",
    "SelfProfile",
]
