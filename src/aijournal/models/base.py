"""Shared base model for aijournal Pydantic schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AijournalModel(BaseModel):
    """Common model config for all repo schemas."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )


__all__ = ["AijournalModel"]
