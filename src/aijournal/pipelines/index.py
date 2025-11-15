"""Pipeline helpers for building and maintaining the retrieval index."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from math import ceil
from pathlib import Path
from typing import Any

import numpy as np

from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.index import Chunk, ChunkBatch, IndexMeta
from aijournal.domain.journal import NormalizedEntry
from aijournal.io.artifacts import save_artifact
from aijournal.io.yaml_io import load_yaml_model
from aijournal.models.authoritative import ManifestEntry
from aijournal.pipelines import normalization
from aijournal.services.embedding import EmbeddingBackend
from aijournal.utils import time as time_utils

CHUNK_TARGET_CHARS = 900
CHUNK_MAX_CHARS = 1200


@dataclass
class ChunkRecord:
    """Normalized chunk + embedding payload stored in SQLite."""

    chunk_id: str
    normalized_id: str
    normalized_path: str
    chunk_index: int
    chunk_type: str
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
                chunk_type="entry",
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


def index_entries(
    tasks: Sequence[IndexTask],
    chunk_index,
    embedder: EmbeddingBackend,
    char_per_token: float,
) -> tuple[dict[str, Any], Mapping[str, list[ChunkRecord]]]:
    touched_dates: set[str] = set()
    processed_entries = 0
    processed_chunks = 0
    records_by_day: dict[str, list[ChunkRecord]] = defaultdict(list)

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
        for chunk, vector in zip(chunk_records, vectors, strict=False):
            chunk.embedding = vector
        chunk_index.replace_entry(task.normalized_id, chunk_records)
        for record in chunk_records:
            records_by_day[record.date].append(record)
        touched_dates.add(task.day)
        processed_entries += 1
        processed_chunks += len(chunk_records)

    stats = {"entries": processed_entries, "chunks": processed_chunks, "dates": touched_dates}
    return stats, records_by_day


def write_chunk_manifests(
    chunk_dir: Path,
    records_by_day: Mapping[str, list[ChunkRecord]],
    embedder: EmbeddingBackend,
) -> None:
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for day in sorted(records_by_day.keys()):
        day_records = records_by_day[day]
        if not day_records:
            continue
        chunks: list[Chunk] = []
        vectors: list[list[float]] = []
        for record in sorted(
            day_records,
            key=lambda item: (item.normalized_id, item.chunk_index),
        ):
            chunks.append(
                Chunk(
                    chunk_id=record.chunk_id,
                    normalized_id=record.normalized_id,
                    chunk_index=record.chunk_index,
                    chunk_type=record.chunk_type,
                    text=record.chunk_text,
                    date=record.date,
                    tags=record.tags,
                    source_type=record.source_type,
                    source_path=record.source_path,
                    tokens=record.tokens,
                    source_hash=record.source_hash,
                    manifest_hash=record.manifest_hash,
                )
            )
            vectors.append(record.embedding or [])

        timestamp = time_utils.format_timestamp(time_utils.now())
        artifact = Artifact[ChunkBatch](
            kind=ArtifactKind.INDEX_CHUNKS,
            meta=ArtifactMeta(
                created_at=timestamp,
                model=embedder.model,
                notes={
                    "vector_dimension": str(embedder.dim),
                    "chunk_count": str(len(chunks)),
                },
            ),
            data=ChunkBatch(day=day, chunks=chunks),
        )

        artifact_path = chunk_dir / f"{day}.yaml"
        save_artifact(artifact_path, artifact)
        vector_array = (
            np.array(vectors, dtype="float32")
            if vectors
            else np.zeros((0, embedder.dim), dtype="float32")
        )
        np.save(chunk_dir / f"{day}.npy", vector_array)


def write_index_meta(
    root: Path,
    *,
    embedder: EmbeddingBackend,
    chunk_total: int,
    entry_total: int,
    mode: str,
    fake_mode: bool,
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
