"""Pipeline helpers for building and maintaining the retrieval index."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from math import ceil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.facts import DailySummary, MicroFactsFile
from aijournal.domain.index import Chunk, ChunkBatch, IndexMeta
from aijournal.domain.journal import NormalizedEntry
from aijournal.io.artifacts import load_artifact_data, save_artifact
from aijournal.io.yaml_io import load_yaml_model
from aijournal.pipelines import normalization
from aijournal.services.summaries import load_daily_summary, summary_artifact_path
from aijournal.utils import time as time_utils

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from aijournal.common.app_config import AppConfig
    from aijournal.models.authoritative import ManifestEntry
    from aijournal.services.embedding import EmbeddingBackend

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


def _derived_artifact_root(workspace: Path, config: AppConfig) -> Path:
    derived = Path(config.paths.derived)
    if not derived.is_absolute():
        derived = workspace / derived
    return derived


def _derived_microfacts_path(workspace: Path, config: AppConfig, day: str) -> Path:
    return _derived_artifact_root(workspace, config) / "microfacts" / f"{day}.yaml"


def _relative_workspace_path(workspace: Path, path: Path) -> str:
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


def _load_daily_microfacts(workspace: Path, config: AppConfig, day: str) -> MicroFactsFile | None:
    path = _derived_microfacts_path(workspace, config, day)
    if not path.exists():
        return None
    try:
        return load_artifact_data(path, MicroFactsFile)
    except Exception:  # pragma: no cover - defensive
        return None


def _summary_chunk_records(
    day: str,
    summary: DailySummary,
    *,
    source_path: str,
    char_per_token: float,
) -> list[ChunkRecord]:
    normalized_id = f"summary-{day}"
    records: list[ChunkRecord] = []
    items: list[tuple[str, str]] = []
    for kind, texts in (
        ("bullet", summary.bullets),
        ("highlight", summary.highlights),
        ("todo", summary.todo_candidates),
    ):
        for text in texts:
            value = (text or "").strip()
            if not value:
                continue
            label = kind.capitalize()
            items.append((kind, f"{label}: {value}"))

    for idx, (kind, text) in enumerate(items):
        records.append(
            ChunkRecord(
                chunk_id=f"{normalized_id}#c{idx}",
                normalized_id=normalized_id,
                normalized_path=source_path,
                chunk_index=idx,
                chunk_type="summary",
                chunk_text=text,
                date=day,
                tags=["summary", kind],
                source_type="summary",
                source_path=source_path,
                tokens=token_estimate(text, char_per_token),
                source_hash=None,
                manifest_hash=None,
            ),
        )
    return records


def _microfact_chunk_records(
    day: str,
    facts: MicroFactsFile,
    *,
    source_path: str,
    char_per_token: float,
) -> list[ChunkRecord]:
    normalized_id = f"microfacts-{day}"
    records: list[ChunkRecord] = []
    for idx, fact in enumerate(facts.facts):
        statement = (fact.statement or "").strip()
        if not statement:
            continue
        prefix = f"{fact.id}: " if fact.id else ""
        text = f"{prefix}{statement}"
        records.append(
            ChunkRecord(
                chunk_id=f"{normalized_id}#c{idx}",
                normalized_id=normalized_id,
                normalized_path=source_path,
                chunk_index=idx,
                chunk_type="microfact",
                chunk_text=text,
                date=day,
                tags=["microfact"],
                source_type="microfact",
                source_path=source_path,
                tokens=token_estimate(text, char_per_token),
                source_hash=None,
                manifest_hash=None,
            ),
        )
    return records


def _embed_chunk_records(embedder: EmbeddingBackend, records: list[ChunkRecord]) -> None:
    if not records:
        return
    texts = [record.chunk_text for record in records]
    vectors = embedder.embed(texts)
    for record, vector in zip(records, vectors, strict=False):
        record.embedding = vector


def _index_derived_chunks(
    workspace: Path,
    config: AppConfig,
    days: Iterable[str],
    chunk_index: Any,
    embedder: EmbeddingBackend,
    char_per_token: float,
    records_by_day: dict[str, list[ChunkRecord]],
    include_summaries: bool,
    include_microfacts: bool,
) -> tuple[int, int]:
    summary_count = 0
    microfact_count = 0
    for day in sorted(days):
        day_records = records_by_day.setdefault(day, [])
        if include_summaries:
            summary = load_daily_summary(workspace, config, day, required=False)
            if summary is not None:
                summary_path = summary_artifact_path(workspace, config, day)
                relative_path = _relative_workspace_path(workspace, summary_path)
                summary_records = _summary_chunk_records(
                    day,
                    summary,
                    source_path=relative_path,
                    char_per_token=char_per_token,
                )
                _embed_chunk_records(embedder, summary_records)
                chunk_index.replace_entry(f"summary-{day}", summary_records)
                day_records.extend(summary_records)
                summary_count += len(summary_records)
        if include_microfacts:
            microfacts = _load_daily_microfacts(workspace, config, day)
            if microfacts is not None:
                microfacts_path = _derived_microfacts_path(workspace, config, day)
                relative_path = _relative_workspace_path(workspace, microfacts_path)
                microfact_records = _microfact_chunk_records(
                    day,
                    microfacts,
                    source_path=relative_path,
                    char_per_token=char_per_token,
                )
                _embed_chunk_records(embedder, microfact_records)
                chunk_index.replace_entry(f"microfacts-{day}", microfact_records)
                day_records.extend(microfact_records)
                microfact_count += len(microfact_records)
    return summary_count, microfact_count


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
        entry.created_at or time_utils.format_timestamp(time_utils.now()),
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
    *,
    workspace: Path,
    config: AppConfig,
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

    base_chunk_total = processed_chunks
    stats = {"entries": processed_entries, "chunks": base_chunk_total, "dates": touched_dates}
    summary_count, microfact_count = _index_derived_chunks(
        workspace,
        config,
        touched_dates,
        chunk_index,
        embedder,
        char_per_token,
        records_by_day,
        include_summaries=config.index.include_summaries,
        include_microfacts=config.index.include_microfacts,
    )
    stats["summary_chunks"] = summary_count
    stats["microfact_chunks"] = microfact_count
    stats["chunks"] = base_chunk_total + summary_count + microfact_count
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
                ),
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
