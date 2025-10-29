"""Strict Pydantic base classes used across the project."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Pydantic model with strict settings and forbidden extras."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        validate_assignment=True,
        arbitrary_types_allowed=False,
        populate_by_name=True,
        protected_namespaces=(),
    )


__all__ = ["StrictModel"]
