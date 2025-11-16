"""Domain models for chat turns and telemetry."""

from __future__ import annotations

from pydantic import Field

from aijournal.api.chat import ChatCitation, ChatResponse  # noqa: TC001
from aijournal.common.base import StrictModel
from aijournal.common.types import TimestampStr  # noqa: TC001
from aijournal.domain.index import RetrievedChunk  # noqa: TC001
from aijournal.domain.persona import PersonaCore  # noqa: TC001


class ChatTelemetry(StrictModel):
    """Telemetry captured during a chat turn."""

    retrieval_ms: float
    chunk_count: int
    retriever_source: str
    model: str


class ChatTurn(StrictModel):
    """Structured representation of a chat turn."""

    question: str
    answer: str
    response: ChatResponse
    persona: PersonaCore
    citations: list[ChatCitation] = Field(default_factory=list)
    retrieved_chunks: list[RetrievedChunk] = Field(default_factory=list)
    fake_mode: bool
    intent: str
    clarifying_question: str | None = None
    telemetry: ChatTelemetry
    timestamp: TimestampStr
