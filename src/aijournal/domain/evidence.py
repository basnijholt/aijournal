"""Domain models for evidence source references."""

from __future__ import annotations

from aijournal.common.base import StrictModel


class SourceRef(StrictModel):
    """Reference to a normalized entry that supports a claim or fact."""

    entry_id: str
