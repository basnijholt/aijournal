"""Pipeline helpers for building and maintaining the retrieval index."""

from __future__ import annotations

import json
import sqlite3
from array import array
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from math import ceil
from pathlib import Path
from typing import Any, Literal

import numpy as np
from annoy import AnnoyIndex

from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.index import IndexMeta
from aijournal.domain.journal import NormalizedEntry
from aijournal.io.artifacts import save_artifact
from aijournal.io.yaml_io import load_yaml_model, write_yaml_model
from aijournal.models.authoritative import ManifestEntry
from aijournal.models.derived import ChunkManifest, ChunkManifestChunk, ChunkManifestMeta
from aijournal.pipelines import normalization
from aijournal.services.embedding import EmbeddingBackend
from aijournal.utils import time as time_utils

CHUNK_TARGET_CHARS = 900
CHUNK_MAX_CHARS = 1200
ANN_METRIC: Literal["angular", "euclidean", "manhattan", "hamming", "dot"] = "angular"


@dataclass
class ChunkRecord:
    """Normalized chunk + embedding payload stored in SQLite."""

    chunk_id: str
    normalized_id: str
    normalized_path: str
    chunk_index: int
    chunk_text: str
    date: str
    tags: list[str]
    source_type: str | None
    source_path: str
    tokens: int
    source_hash: str | None
    manifest_hash: str | None
    embedding: list[float] | None = None


@dataclass
class SourceRecord:
    """Bookkeeping entry for indexed normalized files."""

    normalized_path: str
    normalized_id: str
    date: str
    source_hash: str | None
    manifest_hash: str | None
    chunk_count: int
    updated_at: str


@dataclass
class IndexTask:
    """Prepared normalized entry ready for chunking/indexing."""

    day: str
    path: Path
    normalized_path: str
    normalized_id: str
    entry: NormalizedEntry
    source_hash: str | None
    manifest: ManifestEntry | None


def hash_file(path: Path) -> str | None:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def select_source_hash(entry: NormalizedEntry, path: Path) -> str | None:
    source_hash = entry.source_hash
    if isinstance(source_hash, str) and source_hash.strip():
        return source_hash.strip()
    return hash_file(path)


def prepare_index_tasks(
    entries: Sequence[tuple[str, Path]],
    *,
    root: Path,
    manifest_index: dict[str, ManifestEntry],
    relative_path: Callable[[Path], str],
) -> list[IndexTask]:
    tasks: list[IndexTask] = []
    for day, path in entries:
        entry = load_yaml_model(path, NormalizedEntry)
        normalized_id = entry.id.strip()
        if not normalized_id:
            continue
        normalized_path = relative_path(path)
        manifest = manifest_index.get(normalized_id)
        source_hash = select_source_hash(entry, path)
        if manifest and not source_hash:
            source_hash = manifest.hash
        tasks.append(
            IndexTask(
                day=day,
                path=path,
                normalized_path=normalized_path,
                normalized_id=normalized_id,
                entry=entry,
                source_hash=source_hash,
                manifest=manifest,
            ),
        )
    return tasks


def filter_tasks_for_tail(
    conn: sqlite3.Connection,
    tasks: Sequence[IndexTask],
) -> list[IndexTask]:
    pending: list[IndexTask] = []
    for task in tasks:
        stored = conn.execute(
            "SELECT source_hash FROM sources WHERE normalized_path = ?",
            (task.normalized_path,),
        ).fetchone()
        stored_hash = stored[0] if stored else None
        if stored_hash and task.source_hash and stored_hash == task.source_hash:
            continue
        pending.append(task)
    return pending


def entry_paragraphs(entry: NormalizedEntry) -> list[str]:
    paragraphs: list[str] = []
    summary = entry.summary
    if isinstance(summary, str) and summary.strip():
        paragraphs.append(summary.strip())
    for section in entry.sections or []:
        heading = str(section.heading or "").strip()
        snippet = str(section.summary or "").strip()
        if heading and snippet:
            paragraphs.append(f"{heading}: {snippet}")
        elif heading:
            paragraphs.append(heading)
        elif snippet:
            paragraphs.append(snippet)
    if not paragraphs:
        title = str(entry.title or entry.id or "entry").strip()
        if title:
            paragraphs.append(title)
    return paragraphs


def chunk_paragraphs(paragraphs: Iterable[str]) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for paragraph in paragraphs:
        text = paragraph.strip()
        if not text:
            continue
        if current and length + len(text) + 2 > CHUNK_MAX_CHARS:
            chunks.append("\n\n".join(current))
            current = [text]
            length = len(text)
            continue
        current.append(text)
        length += len(text) + (2 if length else 0)
        if length >= CHUNK_TARGET_CHARS:
            chunks.append("\n\n".join(current))
            current = []
            length = 0
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def token_estimate(text: str, char_per_token: float) -> int:
    divisor = char_per_token if char_per_token > 0 else 4.2
    return max(1, ceil(len(text) / divisor))


def build_chunk_records(
    entry: NormalizedEntry,
    normalized_path: str,
    *,
    char_per_token: float,
    manifest: ManifestEntry | None,
    source_hash: str | None,
) -> list[ChunkRecord]:
    entry_id = entry.id.strip()
    if not entry_id:
        return []
    created_at = normalization.normalize_created_at(
        entry.created_at or time_utils.format_timestamp(time_utils.now())
    )
    date_value = time_utils.created_date(created_at)
    tags = entry.tags or []
    paragraphs = entry_paragraphs(entry)
    chunk_texts = chunk_paragraphs(paragraphs)
    if not chunk_texts:
        chunk_texts = [entry.title or entry_id]

    chunk_records: list[ChunkRecord] = []
    manifest_hash = manifest.hash if manifest else None
    source_type = entry.source_type or (manifest.source_type if manifest else None)

    for idx, text in enumerate(chunk_texts):
        chunk_records.append(
            ChunkRecord(
                chunk_id=f"{entry_id}#c{idx}",
                normalized_id=entry_id,
                normalized_path=normalized_path,
                chunk_index=idx,
                chunk_text=text,
                date=date_value,
                tags=[str(tag) for tag in tags],
                source_type=source_type,
                source_path=entry.source_path or normalized_path,
                tokens=token_estimate(text, char_per_token),
                source_hash=source_hash,
                manifest_hash=str(manifest_hash) if manifest_hash else None,
            ),
        )

    return chunk_records


def delete_chunks_for_entry(conn: sqlite3.Connection, normalized_id: str) -> None:
    rows = conn.execute(
        "SELECT chunk_id FROM chunks WHERE normalized_id = ?",
        (normalized_id,),
    ).fetchall()
    if rows:
        conn.executemany(
            "DELETE FROM chunk_fts WHERE chunk_id = ?",
            ((row[0],) for row in rows),
        )
    conn.execute("DELETE FROM chunks WHERE normalized_id = ?", (normalized_id,))


def vector_to_blob(vector: Sequence[float]) -> memoryview:
    arr = array("f", vector)
    return memoryview(arr.tobytes())


def blob_to_vector(blob: bytes) -> list[float]:
    arr = array("f")
    arr.frombytes(blob)
    return list(arr)


def insert_chunk_record(conn: sqlite3.Connection, chunk: ChunkRecord) -> None:
    if chunk.embedding is None:
        msg = "Chunk embedding missing"
        raise RuntimeError(msg)
    tags_json = json.dumps(chunk.tags, sort_keys=True)
    conn.execute(
        """
        INSERT INTO chunks (
            chunk_id, normalized_id, normalized_path, chunk_index, chunk_text,
            date, tags, source_type, source_path, tokens, source_hash,
            manifest_hash, embedding
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chunk.chunk_id,
            chunk.normalized_id,
            chunk.normalized_path,
            chunk.chunk_index,
            chunk.chunk_text,
            chunk.date,
            tags_json,
            chunk.source_type,
            chunk.source_path,
            chunk.tokens,
            chunk.source_hash,
            chunk.manifest_hash,
            vector_to_blob(chunk.embedding),
        ),
    )
    conn.execute(
        "INSERT INTO chunk_fts (chunk_id, chunk_text) VALUES (?, ?)",
        (chunk.chunk_id, chunk.chunk_text),
    )


def upsert_source_record(conn: sqlite3.Connection, record: SourceRecord) -> None:
    conn.execute(
        """
        INSERT INTO sources (
            normalized_path, normalized_id, date, source_hash, manifest_hash,
            chunk_count, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(normalized_path) DO UPDATE SET
            normalized_id=excluded.normalized_id,
            date=excluded.date,
            source_hash=excluded.source_hash,
            manifest_hash=excluded.manifest_hash,
            chunk_count=excluded.chunk_count,
            updated_at=excluded.updated_at
        """,
        (
            record.normalized_path,
            record.normalized_id,
            record.date,
            record.source_hash,
            record.manifest_hash,
            record.chunk_count,
            record.updated_at,
        ),
    )


def index_entries(
    conn: sqlite3.Connection,
    tasks: Sequence[IndexTask],
    embedder: EmbeddingBackend,
    char_per_token: float,
) -> dict[str, Any]:
    touched_dates: set[str] = set()
    processed_entries = 0
    processed_chunks = 0
    timestamp = time_utils.format_timestamp(time_utils.now())

    for task in tasks:
        chunk_records = build_chunk_records(
            task.entry,
            task.normalized_path,
            char_per_token=char_per_token,
            manifest=task.manifest,
            source_hash=task.source_hash,
        )
        if not chunk_records:
            continue
        vectors = embedder.embed([chunk.chunk_text for chunk in chunk_records])
        delete_chunks_for_entry(conn, task.normalized_id)
        for chunk, vector in zip(chunk_records, vectors, strict=False):
            chunk.embedding = vector
            insert_chunk_record(conn, chunk)
        upsert_source_record(
            conn,
            SourceRecord(
                normalized_path=task.normalized_path,
                normalized_id=task.normalized_id,
                date=chunk_records[0].date,
                source_hash=task.source_hash,
                manifest_hash=chunk_records[0].manifest_hash,
                chunk_count=len(chunk_records),
                updated_at=timestamp,
            ),
        )
        touched_dates.add(task.day)
        processed_entries += 1
        processed_chunks += len(chunk_records)

    return {"entries": processed_entries, "chunks": processed_chunks, "dates": touched_dates}


def rebuild_annoy_index(
    conn: sqlite3.Connection,
    dimension: int,
    ann_trees: int,
    output_path: Path,
) -> None:
    rows = conn.execute(
        "SELECT chunk_id, embedding FROM chunks ORDER BY chunk_id",
    ).fetchall()
    if not rows:
        if output_path.exists():
            output_path.unlink()
        conn.execute("DELETE FROM annoy_map")
        return

    index = AnnoyIndex(dimension, metric=ANN_METRIC)
    for idx, row in enumerate(rows):
        vector = blob_to_vector(row["embedding"])
        index.add_item(idx, vector)
    index.build(ann_trees)
    index.save(str(output_path))

    conn.execute("DELETE FROM annoy_map")
    conn.executemany(
        "INSERT INTO annoy_map (annoy_idx, chunk_id) VALUES (?, ?)",
        ((idx, row["chunk_id"]) for idx, row in enumerate(rows)),
    )


def write_chunk_manifests(
    conn: sqlite3.Connection,
    chunk_dir: Path,
    days: Iterable[str],
    embedder: EmbeddingBackend,
) -> None:
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for day in sorted(set(days)):
        rows = conn.execute(
            """
            SELECT chunk_id, normalized_id, chunk_index, chunk_text, tags,
                   source_type, source_path, tokens, source_hash, manifest_hash, embedding
            FROM chunks
            WHERE date = ?
            ORDER BY normalized_id, chunk_index
            """,
            (day,),
        ).fetchall()
        chunk_models: list[ChunkManifestChunk] = []
        vectors: list[list[float]] = []
        for row in rows:
            tags = json.loads(row["tags"] or "[]")
            chunk_models.append(
                ChunkManifestChunk(
                    chunk_id=str(row["chunk_id"]),
                    normalized_id=str(row["normalized_id"]),
                    chunk_index=int(row["chunk_index"]),
                    chunk_text=str(row["chunk_text"] or ""),
                    tags=list(tags),
                    source_type=row["source_type"],
                    source_path=str(row["source_path"] or ""),
                    tokens=int(row["tokens"] or 0),
                    source_hash=row["source_hash"],
                    manifest_hash=row["manifest_hash"],
                ),
            )
            vectors.append(blob_to_vector(row["embedding"]))

        manifest_path = chunk_dir / f"{day}.yaml"
        manifest = ChunkManifest(
            day=day,
            chunks=chunk_models,
            meta=ChunkManifestMeta(
                embedding_model=embedder.model,
                vector_dimension=embedder.dim,
                generated_at=time_utils.format_timestamp(time_utils.now()),
            ),
        )
        write_yaml_model(manifest_path, manifest)
        vector_array = (
            np.array(vectors, dtype="float32")
            if vectors
            else np.zeros((0, embedder.dim), dtype="float32")
        )
        np.save(chunk_dir / f"{day}.npy", vector_array)


def gather_index_stats(conn: sqlite3.Connection) -> tuple[int, int]:
    chunk_total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    entry_total = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    return int(chunk_total), int(entry_total)


def write_index_meta(
    root: Path,
    *,
    embedder: EmbeddingBackend,
    chunk_total: int,
    entry_total: int,
    mode: str,
    fake_mode: bool,
    ann_trees: int,
    search_k_factor: float,
    char_per_token: float,
    since: str | None,
    limit: int | None,
    touched_dates: Iterable[str],
    index_meta_path: Callable[[Path], Path],
) -> None:
    timestamp = time_utils.format_timestamp(time_utils.now())
    index_meta = IndexMeta(
        embedding_model=embedder.model,
        vector_dimension=embedder.dimension,
        chunk_count=chunk_total,
        entry_count=entry_total,
        mode=mode,
        fake_mode=fake_mode,
        annoy_trees=ann_trees,
        search_k_factor=search_k_factor,
        char_per_token=char_per_token,
        since=since,
        limit=limit,
        touched_dates=sorted(set(touched_dates)),
        updated_at=timestamp,
    )
    artifact = Artifact[IndexMeta](
        kind=ArtifactKind.INDEX_META,
        meta=ArtifactMeta(
            created_at=timestamp,
            model=embedder.model,
        ),
        data=index_meta,
    )
    meta_path = index_meta_path(root)
    artifact_format = meta_path.suffix.lstrip(".") or "json"
    save_artifact(meta_path, artifact, format=artifact_format)
