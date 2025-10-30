"""Shared retrieval service backed by Annoy + SQLite."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any

from annoy import AnnoyIndex

from aijournal.common.schema_mode import allow_legacy_read
from aijournal.domain.index import IndexMeta, RetrievedChunk
from aijournal.io.artifacts import load_artifact

from .embedding import EmbeddingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class RetrievalFilters:
    """Optional filters applied during retrieval."""

    tags: frozenset[str] = field(default_factory=frozenset)
    source_types: frozenset[str] = field(default_factory=frozenset)
    date_from: str | None = None
    date_to: str | None = None


@dataclass(frozen=True)
class RetrievalMeta:
    mode: str
    source: str
    k: int
    fake_mode: bool


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    meta: RetrievalMeta


class Retriever:
    """Retrieval utility that backs chat/advice pipelines."""

    def __init__(
        self,
        root: Path,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.root = Path(root)
        self.config: dict[str, Any] = dict(config or {})
        self.index_dir = self.root / "derived" / "index"
        self.db_path = self.index_dir / "index.db"
        self.annoy_path = self.index_dir / "annoy.index"
        self.meta_path = self.index_dir / "meta.json"
        self.meta_v2_path = self.index_dir / "meta.v2.json"
        self._meta = self._load_meta()
        self._conn_lock = RLock()
        self._conn: sqlite3.Connection | None = None
        self._annoy: AnnoyIndex | None = None
        self._embedder_instance: EmbeddingBackend | None = None
        self._fake_mode = os.getenv("AIJOURNAL_FAKE_OLLAMA") == "1"

        index_cfg_raw = self.config.get("index")
        index_cfg = index_cfg_raw if isinstance(index_cfg_raw, dict) else {}
        self.search_k_factor = float(index_cfg.get("search_k_factor") or 3.0)

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
        if not self._can_use_annoy():
            msg = (
                "Retrieval index not available. Run `aijournal index rebuild` to generate "
                "derived/index/index.db and derived/index/annoy.index before searching."
            )
            raise RuntimeError(msg)

        chunks = self._search_annoy(query, k=k, filters=filters)
        meta = RetrievalMeta(
            mode="annoy",
            source="annoy+sqlite",
            k=k,
            fake_mode=self._fake_mode,
        )
        return RetrievalResult(chunks=chunks, meta=meta)

    # ------------------------------------------------------------------
    # Annoy-backed search
    # ------------------------------------------------------------------
    def _search_annoy(
        self,
        query: str,
        *,
        k: int,
        filters: RetrievalFilters,
    ) -> list[RetrievedChunk]:
        vector = self._get_embedder().embed_one(query)
        index = self._annoy_index()
        candidate_k = max(k, int(k * self.search_k_factor))
        indices, distances = index.get_nns_by_vector(vector, candidate_k, include_distances=True)
        if not indices:
            return []

        distance_map = dict(zip(indices, distances, strict=False))
        chunk_ids: list[str] = []
        with self._conn_guard() as conn:
            mapping = self._load_annoy_map(conn)
            for idx in indices:
                chunk_id = mapping.get(idx)
                if chunk_id:
                    chunk_ids.append(chunk_id)
            if not chunk_ids:
                return []
            rows = self._fetch_chunks(conn, chunk_ids)

        scored: list[RetrievedChunk] = []
        today = datetime.now(tz=UTC).date()
        inverse_map = mapping.inverse
        for chunk_id in chunk_ids:
            row = rows.get(chunk_id)
            if not row:
                continue
            chunk_date = row["date"]
            if not self._passes_filters(chunk_date, row["tags"], row["source_type"], filters):
                continue
            annoy_idx = inverse_map.get(chunk_id)
            distance = 1.0 if annoy_idx is None else distance_map.get(annoy_idx, 1.0)
            cosine = max(0.0, 1.0 - distance)
            recency = self._recency_score(chunk_date, today)
            final_score = 0.7 * cosine + 0.3 * recency
            scored.append(self._row_to_chunk(row, final_score))
            if len(scored) >= k:
                break
        return scored

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def close(self) -> None:
        with self._conn_lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
        self._annoy = None

    def _load_meta(self) -> IndexMeta:
        if self.meta_v2_path.exists():
            try:
                artifact = load_artifact(self.meta_v2_path, IndexMeta)
                return artifact.data
            except Exception:  # pragma: no cover - fallback to legacy path
                pass

        if self.meta_path.exists():
            if not allow_legacy_read():
                msg = (
                    "Legacy index meta not permitted in AIJOURNAL_SCHEMA_MODE; "
                    "run `aijournal ops index rebuild`."
                )
                raise RuntimeError(msg)
            try:
                data = json.loads(self.meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return IndexMeta()
            return IndexMeta.model_validate(data or {})

        return IndexMeta()

    def _can_use_annoy(self) -> bool:
        return self.db_path.exists() and self.annoy_path.exists()

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._conn = conn
        return self._conn

    @contextmanager
    def _conn_guard(self) -> Iterator[sqlite3.Connection]:
        with self._conn_lock:
            yield self._connection()

    def _annoy_index(self) -> AnnoyIndex:
        if self._annoy is None:
            dim = int(self._meta.vector_dimension or self._get_embedder().dim)
            index = AnnoyIndex(dim, metric="angular")
            index.load(str(self.annoy_path))
            self._annoy = index
        return self._annoy

    def _get_embedder(self) -> EmbeddingBackend:
        if self._embedder_instance is None:
            model = str(
                self._meta.embedding_model
                or self.config.get("embedding_model")
                or "embeddinggemma",
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

    def _load_annoy_map(self, conn: sqlite3.Connection) -> AnnoyMap:
        rows = conn.execute("SELECT annoy_idx, chunk_id FROM annoy_map").fetchall()
        return AnnoyMap({int(row["annoy_idx"]): row["chunk_id"] for row in rows})

    def _fetch_chunks(
        self,
        conn: sqlite3.Connection,
        chunk_ids: Sequence[str],
    ) -> dict[str, sqlite3.Row]:
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = conn.execute(
            f"SELECT * FROM chunks WHERE chunk_id IN ({placeholders})",
            tuple(chunk_ids),
        ).fetchall()
        return {row["chunk_id"]: row for row in rows}

    def _passes_filters(
        self,
        date_value: str,
        tags_json: str,
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
            candidate_tags = {tag.lower() for tag in json.loads(tags_json or "[]")}
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

    def _row_to_chunk(self, row: sqlite3.Row, score: float) -> RetrievedChunk:
        tags = json.loads(row["tags"] or "[]")
        keys = set(row.keys())
        source_hash = row["source_hash"] if "source_hash" in keys else None
        manifest_hash = row["manifest_hash"] if "manifest_hash" in keys else None
        return RetrievedChunk(
            chunk_id=row["chunk_id"],
            normalized_id=row["normalized_id"],
            chunk_index=row["chunk_index"],
            text=row["chunk_text"],
            date=row["date"],
            tags=list(tags),
            source_type=row["source_type"],
            source_path=row["source_path"],
            tokens=row["tokens"],
            source_hash=source_hash,
            manifest_hash=manifest_hash,
            score=score,
        )


class AnnoyMap(dict[int, str]):
    """Map from Annoy indices to chunk IDs with inverse lookup."""

    def __init__(self, mapping: dict[int, str]) -> None:
        super().__init__(mapping)
        self.inverse = {chunk_id: idx for idx, chunk_id in mapping.items()}


__all__ = [
    "RetrievalFilters",
    "RetrievalResult",
    "RetrievedChunk",
    "Retriever",
]
