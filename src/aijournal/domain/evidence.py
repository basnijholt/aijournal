"""Domain models for evidence source references."""

from __future__ import annotations

from pydantic import model_validator

from aijournal.common.base import StrictModel


class SourceRef(StrictModel):
    """Reference to a normalized entry or retrieved chunk that supports a claim or fact."""

    entry_id: str | None = None
    paragraph_index: int | None = None
    manifest_hash: str | None = None
    chunk_id: str | None = None
    score: float | None = None

    @model_validator(mode="after")
    def _validate_source(self) -> SourceRef:
        if not self.entry_id and not self.chunk_id:
            msg = "at least one of entry_id or chunk_id must be provided"
            raise ValueError(msg)
        return self
