"""Chroma-backed chunk index for retrieval."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import chromadb
from chromadb.api.types import SparseVector
from chromadb.config import Settings

from aijournal.domain.index import Chunk

if TYPE_CHECKING:
    from chromadb.api import ClientAPI, Collection

    from aijournal.common.app_config import AppConfig


def _resolve_derived_path(workspace: Path, config: AppConfig) -> Path:
    derived = Path(config.paths.derived)
    if not derived.is_absolute():
        derived = workspace / derived
    return derived


MetadataValue = str | int | float | bool | SparseVector | None


def _metadata_from_record(record: Any) -> dict[str, MetadataValue]:
    tags = getattr(record, "tags", []) or []
    tags_json = json.dumps([str(tag) for tag in tags], sort_keys=True)
    meta: dict[str, MetadataValue] = {
        "normalized_id": getattr(record, "normalized_id", None),
        "chunk_index": getattr(record, "chunk_index", 0),
        "date": getattr(record, "date", None),
        "tags": tags_json,
        "source_type": getattr(record, "source_type", None),
        "source_path": getattr(record, "source_path", None),
        "tokens": getattr(record, "tokens", 0),
        "source_hash": getattr(record, "source_hash", None),
        "manifest_hash": getattr(record, "manifest_hash", None),
        "chunk_type": getattr(record, "chunk_type", "entry"),
    }
    return meta


@dataclass(slots=True)
class ChunkIndexHit:
    """Chunk returned from a Chroma similarity query."""

    chunk: Chunk
    distance: float | None


class ChunkIndex:
    """Persistent semantic chunk store backed by ChromaDB."""

    def __init__(
        self,
        workspace: Path,
        config: AppConfig,
        *,
        collection_name: str | None = None,
    ) -> None:
        self.workspace = workspace
        self.config = config
        derived_root = _resolve_derived_path(workspace, config)
        self._index_dir = derived_root / "index"
        self._db_path = self._index_dir / "chroma"
        self._db_path.mkdir(parents=True, exist_ok=True)
        self._collection_name = collection_name or "chunks"
        self._client_settings = Settings(
            anonymized_telemetry=False,
            allow_reset=True,
        )
        self._client = self._create_client()
        self._collection = self._ensure_collection(self._client)

    def _create_client(self) -> ClientAPI:
        return chromadb.PersistentClient(
            path=str(self._db_path),
            settings=self._client_settings,
        )

    def _ensure_collection(self, client: ClientAPI) -> Collection:
        return client.get_or_create_collection(self._collection_name)

    def reset(self) -> None:
        with contextlib.suppress(Exception):  # pragma: no cover - collection may not exist yet
            self._client.delete_collection(self._collection_name)
        self._collection = self._ensure_collection(self._client)

    def replace_entry(self, normalized_id: str, records: Sequence[Any]) -> None:
        if not normalized_id:
            return
        self._collection.delete(where={"normalized_id": normalized_id})
        self.upsert(records)

    def upsert(self, records: Sequence[Any]) -> None:
        pending_ids: list[str] = []
        documents: list[str] = []
        embeddings: list[Sequence[float]] = []
        metadatas: list[Mapping[str, MetadataValue]] = []
        for record in records:
            chunk_id = getattr(record, "chunk_id", None)
            text = getattr(record, "chunk_text", None) or ""
            vector = getattr(record, "embedding", None)
            if not chunk_id or vector is None:
                continue
            pending_ids.append(chunk_id)
            documents.append(text)
            embeddings.append(vector)
            meta = _metadata_from_record(record)
            meta["chunk_id"] = chunk_id
            metadatas.append(meta)
        if not pending_ids:
            return
        self._collection.upsert(
            ids=pending_ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    def delete_all(self) -> None:
        self._collection.delete(where={})

    def count_chunks(self) -> int:
        return int(self._collection.count())

    def count_entries(self) -> int:
        payload = self._collection.get(include=["metadatas"], limit=None)
        raw_metadatas: list[Any] = payload.get("metadatas") or []
        normalized_ids: set[str] = set()
        flat_meta: list[Mapping[str, MetadataValue]] = []
        if raw_metadatas and isinstance(raw_metadatas[0], list):
            for group in raw_metadatas:
                flat_meta.extend(group or [])
        else:
            flat_meta = [meta for meta in raw_metadatas if isinstance(meta, Mapping)]
        for meta in flat_meta:
            normalized_id = meta.get("normalized_id")
            if isinstance(normalized_id, str) and normalized_id:
                normalized_ids.add(normalized_id)
        return len(normalized_ids)

    def query_by_vector(
        self,
        vector: Sequence[float],
        *,
        candidate_k: int,
    ) -> list[ChunkIndexHit]:
        if candidate_k <= 0:
            return []
        payload = self._collection.query(
            query_embeddings=[vector],
            n_results=candidate_k,
            include=["documents", "metadatas", "distances"],
        )
        hits: list[ChunkIndexHit] = []
        ids = payload.get("ids") or [[]]
        documents = payload.get("documents") or [[]]
        metadatas = payload.get("metadatas") or [[]]
        distances = payload.get("distances") or [[]]
        rows = zip(ids[0], documents[0], metadatas[0], distances[0], strict=False)
        for chunk_id, document, metadata, distance in rows:
            if not chunk_id or not isinstance(metadata, dict):
                continue
            chunk = Chunk(
                chunk_id=str(chunk_id),
                normalized_id=str(metadata.get("normalized_id") or ""),
                chunk_index=int(metadata.get("chunk_index") or 0),
                text=document or "",
                date=str(metadata.get("date") or ""),
                tags=_decode_tags(metadata.get("tags")),
                source_type=metadata.get("source_type"),
                source_path=str(metadata.get("source_path") or ""),
                tokens=int(metadata.get("tokens") or 0),
                source_hash=metadata.get("source_hash"),
                manifest_hash=metadata.get("manifest_hash"),
                chunk_type=str(metadata.get("chunk_type") or "entry"),
            )
            hits.append(
                ChunkIndexHit(
                    chunk=chunk,
                    distance=float(distance) if distance is not None else None,
                ),
            )
        return hits

    def is_ready(self) -> bool:
        return self.count_chunks() > 0


def _decode_tags(raw: Any) -> list[str]:
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, list):
                return [str(tag) for tag in decoded]
        except json.JSONDecodeError:
            return [raw]
    if isinstance(raw, list):
        return [str(tag) for tag in raw]
    return []
