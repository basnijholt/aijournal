"""Structured models for persisted chat session artifacts."""

from __future__ import annotations

from pydantic import Field

from aijournal.common.base import StrictModel
from aijournal.common.types import TimestampStr


class ChatTelemetryRecord(StrictModel):
    """Telemetry captured for a chat turn."""

    retrieval_ms: float
    chunk_count: int
    retriever_source: str
    model: str


class ChatTranscriptTurn(StrictModel):
    """Captured question/answer pair within a chat transcript."""

    turn_index: int
    timestamp: TimestampStr
    question: str
    answer: str
    intent: str
    citations: list[str] = Field(default_factory=list)
    clarifying_question: str | None = None
    telemetry: ChatTelemetryRecord
    feedback: str | None = None
    fake_mode: bool


class ChatTranscript(StrictModel):
    """Artifact describing a full chat session transcript."""

    session_id: str
    created_at: TimestampStr
    updated_at: TimestampStr
    turns: list[ChatTranscriptTurn] = Field(default_factory=list)


__all__ = [
    "ChatTelemetryRecord",
    "ChatTranscript",
    "ChatTranscriptTurn",
]
