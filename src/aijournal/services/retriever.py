"""Shared retrieval service covering Annoy+SQLite and fallback modes."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from annoy import AnnoyIndex

from .embedding import EmbeddingBackend


@dataclass(frozen=True)
class RetrievalFilters:
    """Optional filters applied during retrieval."""

    tags: frozenset[str] = field(default_factory=frozenset)
    source_types: frozenset[str] = field(default_factory=frozenset)
    date_from: str | None = None
    date_to: str | None = None


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    normalized_id: str
    chunk_index: int
    text: str
    date: str
    tags: list[str]
    source_type: str | None
    source_path: str
    tokens: int
    score: float


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    meta: dict[str, Any]


class Retriever:
    """Retrieval utility that backs chat/advice pipelines."""

    def __init__(
        self,
        root: Path,
        config: dict[str, Any] | None = None,
        *,
        force_fallback: bool = False,
    ) -> None:
        self.root = Path(root)
        self.config: dict[str, Any] = dict(config or {})
        self.force_fallback = force_fallback
        self.index_dir = self.root / "derived" / "index"
        self.db_path = self.index_dir / "index.db"
        self.annoy_path = self.index_dir / "annoy.index"
        self.chunk_dir = self.index_dir / "chunks"
        self.meta_path = self.index_dir / "meta.json"
        self._meta = self._load_meta()
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
        force_fallback: bool | None = None,
    ) -> RetrievalResult:
        query = query.strip()
        if not query:
            msg = "Query text is required"
            raise ValueError(msg)
        filters = filters or RetrievalFilters()
        use_fallback = force_fallback if force_fallback is not None else self.force_fallback

        if self._can_use_annoy() and not use_fallback:
            chunks = self._search_annoy(query, k=k, filters=filters)
            mode = "annoy"
            meta = {
                "mode": mode,
                "source": "annoy+sqlite",
                "k": k,
                "fake_mode": self._fake_mode,
            }
            return RetrievalResult(chunks=chunks, meta=meta)

        chunks = self._search_fallback(query, k=k, filters=filters)
        meta = {
            "mode": "fake(fallback)",
            "source": "chunk_manifests",
            "k": k,
            "fake_mode": self._fake_mode,
        }
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

        conn = self._connection()
        mapping = self._load_annoy_map(conn)
        distance_map = dict(zip(indices, distances, strict=False))
        chunk_ids: list[str] = []
        for idx in indices:
            chunk_id = mapping.get(idx)
            if chunk_id:
                chunk_ids.append(chunk_id)
        if not chunk_ids:
            return []

        rows = self._fetch_chunks(conn, chunk_ids)
        scored: list[RetrievedChunk] = []
        today = datetime.now(tz=UTC).date()
        for chunk_id in chunk_ids:
            row = rows.get(chunk_id)
            if not row:
                continue
            chunk_date = row["date"]
            if not self._passes_filters(chunk_date, row["tags"], row["source_type"], filters):
                continue
            annoy_idx = mapping.inverse.get(chunk_id)
            if annoy_idx is None:
                distance = 1.0
            else:
                distance = distance_map.get(annoy_idx, 1.0)
            cosine = max(0.0, 1.0 - distance)
            recency = self._recency_score(chunk_date, today)
            final_score = 0.7 * cosine + 0.3 * recency
            scored.append(self._row_to_chunk(row, final_score))
            if len(scored) >= k:
                break
        return scored

    # ------------------------------------------------------------------
    # Fallback search over chunk manifests (no DB/Annoy required)
    # ------------------------------------------------------------------
    def _search_fallback(
        self,
        query: str,
        *,
        k: int,
        filters: RetrievalFilters,
    ) -> list[RetrievedChunk]:
        manifests = sorted(self.chunk_dir.glob("*.yaml"))
        if not manifests:
            return []
        terms = [term for term in re.split(r"\W+", query.lower()) if term]
        today = datetime.now(tz=UTC).date()
        results: list[RetrievedChunk] = []

        for manifest_path in manifests:
            payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            day = payload.get("day") or manifest_path.stem
            for chunk in payload.get("chunks", []):
                chunk_date = str(chunk.get("date") or day)
                tags = chunk.get("tags") or []
                if not self._passes_filters(
                    chunk_date,
                    json.dumps(tags),
                    chunk.get("source_type"),
                    filters,
                ):
                    continue
                text = str(chunk.get("chunk_text") or "")
                base = self._text_score(text.lower(), terms)
                if base == 0.0:
                    continue
                recency = self._recency_score(chunk_date, today)
                final = 0.7 * base + 0.3 * recency
                results.append(
                    RetrievedChunk(
                        chunk_id=str(chunk.get("chunk_id")),
                        normalized_id=str(chunk.get("normalized_id")),
                        chunk_index=int(chunk.get("chunk_index", 0)),
                        text=text,
                        date=chunk_date,
                        tags=list(tags),
                        source_type=chunk.get("source_type"),
                        source_path=str(chunk.get("source_path") or ""),
                        tokens=int(chunk.get("tokens") or 0),
                        score=final,
                    ),
                )

        results.sort(key=lambda item: item.score, reverse=True)
        return results[:k]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._annoy = None

    def _load_meta(self) -> dict[str, Any]:
        if not self.meta_path.exists():
            return {}
        try:
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _can_use_annoy(self) -> bool:
        return not self.force_fallback and self.db_path.exists() and self.annoy_path.exists()

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            self._conn = conn
        return self._conn

    def _annoy_index(self) -> AnnoyIndex:
        if self._annoy is None:
            dim = int(self._meta.get("vector_dimension") or self._get_embedder().dim)
            index = AnnoyIndex(dim, metric="angular")
            index.load(str(self.annoy_path))
            self._annoy = index
        return self._annoy

    def _get_embedder(self) -> EmbeddingBackend:
        if self._embedder_instance is None:
            model = str(
                self._meta.get("embedding_model")
                or self.config.get("embedding_model")
                or "nomic-embed-text",
            )
            host = os.getenv("AIJOURNAL_OLLAMA_HOST")
            dimension = self._meta.get("vector_dimension")
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
            score=score,
        )

    def _text_score(self, text: str, terms: Sequence[str]) -> float:
        if not terms:
            return 0.0
        hits = sum(text.count(term) for term in terms)
        if hits == 0:
            return 0.0
        return hits / max(1, len(text)) * 10


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
