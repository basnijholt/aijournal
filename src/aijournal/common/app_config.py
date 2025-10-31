"""Typed application configuration model."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class AppConfig(BaseModel):
    """Project configuration backed by Pydantic validation."""

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    model: str | None = None
    host: str | None = None
    temperature: float | None = None
    seed: int | None = None
    max_tokens: int | None = None
    timeout: float | None = None
    paths: dict[str, Any] | None = None
    impact_weights: dict[str, Any] | None = None
    advisor: dict[str, Any] | None = None
    token_estimator: dict[str, Any] | None = None
    persona: dict[str, Any] | None = None
    index: dict[str, Any] | None = None
    chat: dict[str, Any] | None = None
    capture: dict[str, Any] | None = None
    embedding_model: str | None = None

    def to_dict(self, *, exclude_none: bool = False) -> dict[str, Any]:
        """Return the configuration as a plain dictionary."""

        base = self.model_dump(mode="python", exclude_none=exclude_none)
        extras = getattr(self, "model_extra", None)
        if extras:
            base.update(extras)
        return base
