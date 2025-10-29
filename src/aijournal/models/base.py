"""Shared base model for aijournal Pydantic schemas."""

from __future__ import annotations

from aijournal.common import StrictModel


class AijournalModel(StrictModel):
    """Project-specific base model that inherits strict settings."""


__all__ = ["AijournalModel"]
