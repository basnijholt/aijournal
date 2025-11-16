"""Shared retrieval service backed by ChromaDB."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field

from aijournal.common.app_config import AppConfig
from aijournal.common.base import StrictModel
from aijournal.domain.index import IndexMeta, RetrievedChunk
from aijournal.io.artifacts import load_artifact
from aijournal.services.chunk_index import ChunkIndex
from aijournal.services.embedding import EmbeddingBackend

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Sequence


class RetrievalFilters(StrictModel):
    """Optional filters applied during retrieval."""

    model_config = ConfigDict(frozen=True)

    tags: frozenset[str] = Field(default_factory=frozenset)
    source_types: frozenset[str] = Field(default_factory=frozenset)
    date_from: str | None = None
    date_to: str | None = None


class RetrievalMeta(StrictModel):
    """Metadata describing a retrieval invocation."""

    model_config = ConfigDict(frozen=True)

    mode: str
    source: str
    k: int
    fake_mode: bool


class RetrievalResult(StrictModel):
    """Chunks plus metadata returned from a search."""

    chunks: list[RetrievedChunk]
    meta: RetrievalMeta


class Retriever:
    """Retrieval utility that backs chat/advice pipelines."""

    def __init__(
        self,
        root: Path,
        config: AppConfig | None = None,
    ) -> None:
        self.root = Path(root)
        self.config: AppConfig = config or AppConfig()
        self.index_dir = self.root / "derived" / "index"
        self.meta_path = self.index_dir / "meta.json"
        self._meta = self._load_meta()
        self._embedder_instance: EmbeddingBackend | None = None
        self._fake_mode = os.getenv("AIJOURNAL_FAKE_OLLAMA") == "1"
        self._chunk_index = ChunkIndex(self.root, self.config)
        self.search_k_factor = max(1.0, float(self.config.index.search_k_factor or 1.0))

    def search(
        self,
        query: str,
        *,
        k: int = 8,
        filters: RetrievalFilters | None = None,
    ) -> RetrievalResult:
        query = query.strip()
        if not query:
            msg = "Query text is required"
            raise ValueError(msg)
        filters = filters or RetrievalFilters()
        if not self._has_index_artifacts():
            msg = (
                "Retrieval index not available. Run `aijournal index rebuild` to generate the "
                "Chroma chunk index before searching."
            )
            raise RuntimeError(msg)
        if not self._chunk_index.is_ready():
            msg = (
                "Retrieval index not available. Run `aijournal index rebuild` to generate the "
                "Chroma chunk index before searching."
            )
            raise RuntimeError(msg)

        chunks = self._search_chunks(query, k=k, filters=filters)
        meta = RetrievalMeta(
            mode="chroma",
            source="chroma",
            k=k,
            fake_mode=self._fake_mode,
        )
        return RetrievalResult(chunks=chunks, meta=meta)

    def close(self) -> None:
        """No-op retained for compatibility with previous API."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _search_chunks(
        self,
        query: str,
        *,
        k: int,
        filters: RetrievalFilters,
    ) -> list[RetrievedChunk]:
        vector = self._get_embedder().embed_one(query)
        candidate_k = max(k, int(k * self.search_k_factor))
        hits = self._chunk_index.query_by_vector(vector, candidate_k=candidate_k)
        if not hits:
            return []

        scored: list[RetrievedChunk] = []
        today = datetime.now(tz=UTC).date()
        for hit in hits:
            chunk = hit.chunk
            if not chunk.text:
                continue
            if not self._passes_filters(chunk.date, chunk.tags, chunk.source_type, filters):
                continue
            distance = 1.0 if hit.distance is None else float(hit.distance)
            cosine = max(0.0, 1.0 - distance)
            recency = self._recency_score(chunk.date, today)
            final_score = 0.7 * cosine + 0.3 * recency
            scored.append(
                RetrievedChunk(
                    chunk_id=chunk.chunk_id,
                    normalized_id=chunk.normalized_id,
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    date=chunk.date,
                    tags=chunk.tags,
                    source_type=chunk.source_type,
                    source_path=chunk.source_path,
                    tokens=chunk.tokens,
                    source_hash=chunk.source_hash,
                    manifest_hash=chunk.manifest_hash,
                    chunk_type=chunk.chunk_type,
                    score=final_score,
                ),
            )
            if len(scored) >= k:
                break
        return scored

    def _load_meta(self) -> IndexMeta:
        if not self.meta_path.exists():
            return IndexMeta()

        try:
            artifact = load_artifact(self.meta_path, IndexMeta)
        except Exception as exc:  # pragma: no cover - invalid artifact on disk
            msg = (
                f"Index metadata at {self.meta_path} is incompatible with the strict schema. "
                "Delete the file and run `aijournal ops index rebuild`."
            )
            raise RuntimeError(msg) from exc
        return artifact.data

    def _get_embedder(self) -> EmbeddingBackend:
        if self._embedder_instance is None:
            from aijournal.common.constants import DEFAULT_EMBEDDING_MODEL

            env_model = os.getenv("AIJOURNAL_EMBEDDING_MODEL")
            model = str(
                env_model
                or self._meta.embedding_model
                or self.config.embedding_model
                or DEFAULT_EMBEDDING_MODEL,
            )
            host = os.getenv("AIJOURNAL_OLLAMA_HOST")
            dimension = self._meta.vector_dimension
            self._embedder_instance = EmbeddingBackend(
                model,
                host=host,
                fake_mode=self._fake_mode,
                dimension=dimension,
            )
        return self._embedder_instance

    def _passes_filters(
        self,
        date_value: str,
        tags: Sequence[str],
        source_type: str | None,
        filters: RetrievalFilters,
    ) -> bool:
        if filters.date_from and date_value < filters.date_from:
            return False
        if filters.date_to and date_value > filters.date_to:
            return False
        if filters.source_types and (source_type or "").lower() not in {
            value.lower() for value in filters.source_types
        }:
            return False
        if filters.tags:
            candidate_tags = {tag.lower() for tag in tags}
            filter_tags = {tag.lower() for tag in filters.tags}
            if not filter_tags.intersection(candidate_tags):
                return False
        return True

    def _recency_score(self, date_str: str, today: date) -> float:
        try:
            chunk_date = datetime.fromisoformat(date_str).date()
        except ValueError:
            chunk_date = today
        days = max(0, (today - chunk_date).days)
        return 1.0 / (1.0 + 0.05 * days)

    def _has_index_artifacts(self) -> bool:
        chroma_dir = self.index_dir / "chroma"
        if not chroma_dir.exists():
            return False
        return any(chroma_dir.iterdir())
