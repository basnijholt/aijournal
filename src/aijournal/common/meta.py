"""Artifact envelope primitives shared across aijournal."""

from __future__ import annotations

from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import Field

from .base import StrictModel
from .types import TimestampStr  # noqa: TC001

T = TypeVar("T")


class ArtifactMeta(StrictModel):
    """Metadata describing how an artifact was produced."""

    created_at: TimestampStr
    model: str | None = None
    prompt_path: str | None = None
    prompt_hash: str | None = None
    prompt_kind: str | None = None
    prompt_set: str | None = None
    char_per_token: float | None = None
    sources: dict[str, str] | None = None
    notes: dict[str, str] | None = None


class ArtifactKind(StrEnum):
    """Enumeration of persisted artifact categories."""

    PERSONA_CORE = "persona.core"
    SUMMARY_DAILY = "summaries.daily"
    MICROFACTS_DAILY = "microfacts.daily"
    MICROFACTS_CONSOLIDATED = "microfacts.consolidated"
    MICROFACTS_LOG = "microfacts.log"
    PROFILE_PROPOSALS = "profile.proposals"
    PROFILE_UPDATES = "profile.updates"
    FEEDBACK_BATCH = "feedback.batch"
    INDEX_META = "index.meta"
    INDEX_CHUNKS = "index.chunks"
    PACK_L1 = "pack.L1"
    PACK_L2 = "pack.L2"
    PACK_L3 = "pack.L3"
    PACK_L4 = "pack.L4"
    CHAT_TRANSCRIPT = "chat.transcript"
    CHAT_SUMMARY = "chat.summary"
    CHAT_LEARNINGS = "chat.learnings"
    ADVICE_CARD = "advice.card"


class Artifact(StrictModel, Generic[T]):
    """Artifact envelope wrapping a payload of type ``T``."""

    kind: ArtifactKind
    meta: ArtifactMeta
    data: T


class LLMResult(StrictModel, Generic[T]):
    """Captured LLM invocation details paired with the structured payload."""

    model: str
    prompt_path: str
    prompt_hash: str | None = None
    prompt_kind: str | None = None
    prompt_set: str | None = None
    created_at: TimestampStr
    payload: T
    attempts: int = 1
    coercions_applied: list[dict[str, str]] = Field(default_factory=list)
