"""Public capture API models."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from aijournal.common.base import StrictModel
from aijournal.common.constants import DEFAULT_LLM_RETRIES


class CaptureRequest(StrictModel):
    """User-facing capture options supplied by CLI or HTTP."""

    source: Literal["stdin", "editor", "file", "dir"]
    text: str | None = None
    paths: list[str] = Field(default_factory=list)
    source_type: Literal["journal", "notes", "blog"] = "journal"
    date: str | None = None
    title: str | None = None
    slug: str | None = None
    tags: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    mood: str | None = None
    apply_profile: Literal["auto", "review"] = "auto"
    rebuild: Literal["auto", "always", "skip"] = "auto"
    pack: Literal["L1", "L3", "L4"] | None = None
    retries: int = Field(DEFAULT_LLM_RETRIES, ge=0)
    progress: bool = True
    dry_run: bool = False
    snapshot: bool = True


class CaptureInput(CaptureRequest):
    """Internal capture payload enriched with stage bounds."""

    min_stage: int = Field(0, ge=0)
    max_stage: int = Field(7, ge=0)

    @classmethod
    def from_request(
        cls,
        request: CaptureRequest,
        *,
        min_stage: int,
        max_stage: int,
    ) -> CaptureInput:
        payload = request.model_dump(mode="python")
        payload.update({"min_stage": min_stage, "max_stage": max_stage})
        return cls.model_validate(payload)
