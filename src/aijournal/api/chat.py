"""Strict chat API models shared by CLI and services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from aijournal.common.base import StrictModel

if TYPE_CHECKING:
    from aijournal.domain.index import RetrievedChunk


class ChatCitation(StrictModel):
    """Reference to a retrieved chunk included in a chat response."""

    chunk_id: str
    code: str
    normalized_id: str
    chunk_index: int
    source_path: str
    date: str
    tags: list[str] = Field(default_factory=list)
    score: float
    chunk_type: str

    @property
    def marker(self) -> str:
        label = self.chunk_type or "entry"
        return f"[{label}:{self.code}]"

    @classmethod
    def from_chunk(cls, chunk: RetrievedChunk) -> ChatCitation:
        code = f"{chunk.normalized_id}#p{chunk.chunk_index}"
        return cls(
            chunk_id=chunk.chunk_id,
            code=code,
            normalized_id=chunk.normalized_id,
            chunk_index=chunk.chunk_index,
            source_path=chunk.source_path,
            date=chunk.date,
            tags=list(chunk.tags),
            score=chunk.score,
            chunk_type=chunk.chunk_type or "entry",
        )


class ChatCitationRef(StrictModel):
    """Reference emitted by the LLM; resolved against retrieved chunks."""

    code: str = Field(min_length=1)


class ChatResponse(StrictModel):
    """Structured response returned by the chat LLM."""

    answer: str = Field(..., max_length=4000)
    citations: list[ChatCitationRef] = Field(default_factory=list)
    clarifying_question: str | None = None
    telemetry: dict[str, Any] = Field(default_factory=dict)
    timestamp: str | None = None


class ChatRequest(StrictModel):
    """Incoming chat payload for both CLI and FastAPI surfaces."""

    question: str = Field(min_length=1)
    top: int | None = Field(default=None, ge=1)
    tags: list[str] | None = None
    source: list[str] | None = None
    date_from: str | None = None
    date_to: str | None = None
    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.\-]+$")
    save: bool = True
    feedback: Literal["up", "down"] | None = None
