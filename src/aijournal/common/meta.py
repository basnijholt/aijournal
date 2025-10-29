"""Artifact envelope primitives shared across aijournal."""

from __future__ import annotations

from enum import StrEnum
from typing import Generic, Literal, TypeVar

from pydantic import Field

from .base import StrictModel
from .types import TimestampStr

T = TypeVar("T")


class ArtifactMeta(StrictModel):
    """Metadata describing how an artifact was produced."""

    created_at: TimestampStr
    model: str | None = None
    prompt_path: str | None = None
    prompt_hash: str | None = None
    char_per_token: float | None = None
    sources: dict[str, str] | None = None
    notes: dict[str, str] | None = None


class ArtifactKind(StrEnum):
    """Enumeration of persisted artifact categories."""

    PERSONA_CORE = "persona.core"
    SUMMARY_DAILY = "summaries.daily"
    MICROFACTS_DAILY = "microfacts.daily"
    PROFILE_SUGGESTIONS = "profile.suggestions"
    PROFILE_UPDATES = "profile.updates"
    FEEDBACK_BATCH = "feedback.batch"
    INDEX_META = "index.meta"
    INDEX_CHUNKS = "index.chunks"
    PACK_L1 = "pack.L1"
    PACK_L3 = "pack.L3"
    PACK_L4 = "pack.L4"
    CHAT_TRANSCRIPT = "chat.transcript"


class Artifact(StrictModel, Generic[T]):
    """Versioned artifact envelope wrapping a payload of type ``T``."""

    kind: ArtifactKind
    schema_: Literal["v2"] = Field("v2", alias="schema")
    meta: ArtifactMeta
    data: T

    @property
    def schema_version(self) -> Literal["v2"]:
        return self.schema_


class LLMResult(StrictModel, Generic[T]):
    """Captured LLM invocation details paired with the structured payload."""

    model: str
    prompt_path: str
    prompt_hash: str | None = None
    created_at: TimestampStr
    payload: T


__all__ = [
    "ArtifactMeta",
    "ArtifactKind",
    "Artifact",
    "LLMResult",
]
