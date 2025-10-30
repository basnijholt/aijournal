"""Strict chat API models shared by CLI and services."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from aijournal.common.base import StrictModel
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

    @property
    def marker(self) -> str:
        return f"[entry:{self.code}]"

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
        )


class ChatResponse(StrictModel):
    """Structured response returned by the chat LLM."""

    answer: str = Field(..., max_length=4000)
    citations: list[str] = Field(default_factory=list)
    clarifying_question: str | None = None
    telemetry: dict[str, Any] = Field(default_factory=dict)
    timestamp: str | None = None
