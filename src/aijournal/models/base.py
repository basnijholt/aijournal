"""Shared base model for aijournal Pydantic schemas."""

from __future__ import annotations

from aijournal.common.base import StrictModel


class AijournalModel(StrictModel):
    """Project-specific base model that inherits strict settings."""
