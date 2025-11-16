"""Structured models for persisted chat session artifacts."""

from __future__ import annotations

from pydantic import Field

from aijournal.common.base import StrictModel
from aijournal.common.types import TimestampStr  # noqa: TC001
from aijournal.domain.chat import ChatTelemetry  # noqa: TC001


class ChatTranscriptTurn(StrictModel):
    """Captured question/answer pair within a chat transcript."""

    turn_index: int
    timestamp: TimestampStr
    question: str
    answer: str
    intent: str
    citations: list[str] = Field(default_factory=list)
    clarifying_question: str | None = None
    telemetry: ChatTelemetry
    feedback: str | None = None
    fake_mode: bool


class ChatTranscript(StrictModel):
    """Artifact describing a full chat session transcript."""

    session_id: str
    created_at: TimestampStr
    updated_at: TimestampStr
    turns: list[ChatTranscriptTurn] = Field(default_factory=list)


class ChatSessionSummary(StrictModel):
    """Aggregated summary metadata for a chat session."""

    session_id: str
    created_at: TimestampStr
    updated_at: TimestampStr
    turn_count: int = 0
    intent_counts: dict[str, int] = Field(default_factory=dict)
    last_question: str | None = None
    last_answer_preview: str | None = None
    last_citations: list[str] = Field(default_factory=list)
    last_clarifying_question: str | None = None
    last_retrieval_ms: float | None = None
    last_feedback: str | None = None


class ChatLearningEntry(StrictModel):
    """Entry capturing a single learning from a chat turn."""

    turn_index: int
    question: str
    intent: str
    citations: list[str] = Field(default_factory=list)
    clarifying_question: str | None = None
    telemetry: ChatTelemetry
    feedback: str | None = None


class ChatSessionLearnings(StrictModel):
    """Rollup of learnings captured across a chat session."""

    session_id: str
    created_at: TimestampStr
    updated_at: TimestampStr
    learnings: list[ChatLearningEntry] = Field(default_factory=list)
