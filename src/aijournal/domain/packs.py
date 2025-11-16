"""Strict models representing export packs."""

from __future__ import annotations

from pydantic import Field

from aijournal.common.base import StrictModel
from aijournal.common.types import TimestampStr  # noqa: TC001


class PackEntry(StrictModel):
    """Single file included in an export pack."""

    role: str
    path: str
    tokens: int
    content: str


class TrimmedFile(StrictModel):
    """Record of a file trimmed due to token budget limits."""

    role: str
    path: str


class PackMeta(StrictModel):
    """Metadata describing the assembled pack."""

    total_tokens: int
    max_tokens: int
    trimmed: list[TrimmedFile] = Field(default_factory=list)
    generated_at: TimestampStr


class PackBundle(StrictModel):
    """Structured representation of a pack export."""

    level: str
    date: str
    files: list[PackEntry] = Field(default_factory=list)
    meta: PackMeta
