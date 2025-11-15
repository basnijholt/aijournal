"""Domain models for retrieval chunks and index metadata."""

from __future__ import annotations

from pydantic import Field

from aijournal.common.base import StrictModel


class Chunk(StrictModel):
    """Normalized chunk persisted in the retrieval index."""

    chunk_id: str
    normalized_id: str
    chunk_index: int
    text: str
    chunk_type: str = "entry"
    date: str
    tags: list[str] = Field(default_factory=list)
    source_type: str | None = None
    source_path: str
    tokens: int
    source_hash: str | None = None
    manifest_hash: str | None = None


class RetrievedChunk(Chunk):
    """Chunk returned from retrieval with a similarity score."""

    score: float


class IndexMeta(StrictModel):
    """Metadata describing the current retrieval index state."""

    embedding_model: str | None = None
    vector_dimension: int | None = None
    chunk_count: int | None = None
    entry_count: int | None = None
    mode: str | None = None
    fake_mode: bool | None = None
    search_k_factor: float | None = None
    char_per_token: float | None = None
    since: str | None = None
    limit: int | None = None
    touched_dates: list[str] = Field(default_factory=list)
    updated_at: str | None = None


class ChunkBatch(StrictModel):
    """Exported chunk set for a given journal day."""

    day: str
    chunks: list[Chunk] = Field(default_factory=list)
