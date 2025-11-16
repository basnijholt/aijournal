"""Journal domain models for normalized entries and sections."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from aijournal.common.base import StrictModel
from aijournal.common.types import TimestampStr  # noqa: TC001


class Section(StrictModel):
    """Normalized representation of a markdown heading or section."""

    heading: str
    level: int = 1
    summary: str | None = None
    para_index: int | None = None


class NormalizedEntity(StrictModel):
    """Structured entity extracted during normalization."""

    type: str
    value: str
    extra: dict[str, Any] = Field(default_factory=dict)


class NormalizedEntry(StrictModel):
    """Machine-readable journal entry used throughout pipelines."""

    id: str
    created_at: TimestampStr
    source_path: str
    title: str
    tags: list[str] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    entities: list[NormalizedEntity] = Field(default_factory=list)
    summary: str | None = None
    content: str | None = None
    source_hash: str | None = None
    source_type: str | None = None
