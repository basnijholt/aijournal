from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from aijournal.common.app_config import AppConfig
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.evidence import SourceRef
from aijournal.domain.facts import DailySummary, MicroFact, MicroFactsFile
from aijournal.domain.index import ChunkBatch, IndexMeta
from aijournal.domain.journal import NormalizedEntry
from aijournal.io.artifacts import load_artifact, load_artifact_data, save_artifact
from aijournal.io.yaml_io import write_yaml_model
from aijournal.models.authoritative import ManifestEntry
from aijournal.pipelines import index as index_pipeline
from aijournal.services.embedding import EmbeddingBackend
from aijournal.utils import time as time_utils

if TYPE_CHECKING:
    from pathlib import Path


class FakeChunkIndex:
    def __init__(self) -> None:
        self.entries: dict[str, list[index_pipeline.ChunkRecord]] = {}

    def replace_entry(self, normalized_id: str, records: list[index_pipeline.ChunkRecord]) -> None:
        self.entries[normalized_id] = list(records)


def _normalized_entry(entry_id: str) -> NormalizedEntry:
    return NormalizedEntry(
        id=entry_id,
        created_at="2024-01-02T09:00:00Z",
        source_path=f"data/{entry_id}.md",
        title="Focus Session",
        tags=["focus"],
        sections=[],
        summary="Concentrated effort on deep work",
    )


def test_prepare_index_tasks_uses_relative_path(tmp_path: Path) -> None:
    root = tmp_path
    entry_path = root / "data" / "normalized" / "2024-01-02" / "entry.yaml"
    entry_path.parent.mkdir(parents=True, exist_ok=True)
    entry = _normalized_entry("entry-1")
    entry.source_hash = None
    write_yaml_model(entry_path, entry)

    manifest = ManifestEntry(
        hash="manifest-hash",
        path="data/raw.md",
        normalized="data/normalized.yaml",
        source_type="markdown",
        ingested_at="2024-01-02T09:05:00Z",
        created_at="2024-01-02T08:55:00Z",
        id="entry-1",
        tags=["focus"],
    )

    def relative_path(path: Path) -> str:
        return path.name

    tasks = index_pipeline.prepare_index_tasks(
        [("2024-01-02", entry_path)],
        root=root,
        manifest_index={"entry-1": manifest},
        relative_path=relative_path,
    )

    assert len(tasks) == 1
    task = tasks[0]
    assert task.normalized_path == "entry.yaml"
    assert task.source_hash == index_pipeline.hash_file(entry_path)


def test_index_entries_upserts_records(tmp_path: Path) -> None:
    entry = _normalized_entry("entry-1")
    tasks = [
        index_pipeline.IndexTask(
            day="2024-01-02",
            path=tmp_path / "entry.yaml",
            normalized_path="entry.yaml",
            normalized_id="entry-1",
            entry=entry,
            source_hash="hash-1",
            manifest=None,
        ),
    ]

    embedder = EmbeddingBackend(model="fake", fake_mode=True)
    chunk_index = FakeChunkIndex()
    stats, records_by_day = index_pipeline.index_entries(
        tasks,
        chunk_index,
        embedder,
        char_per_token=4.0,
        workspace=tmp_path,
        config=AppConfig(),
    )
    assert stats["entries"] == 1
    assert stats["chunks"] >= 1
    assert "entry-1" in chunk_index.entries
    assert "2024-01-02" in records_by_day


def test_write_index_meta(tmp_path: Path) -> None:
    root = tmp_path
    embedder = EmbeddingBackend(model="fake", fake_mode=True)
    meta_path = tmp_path / "index" / "meta.json"

    index_pipeline.write_index_meta(
        root,
        embedder=embedder,
        chunk_total=10,
        entry_total=5,
        mode="rebuild",
        fake_mode=True,
        search_k_factor=3.0,
        char_per_token=4.2,
        since="2024-01-01",
        limit=None,
        touched_dates={"2024-01-02"},
        index_meta_path=lambda base: meta_path,
    )

    meta = load_artifact_data(meta_path, IndexMeta)
    assert meta.chunk_count == 10
    assert meta.search_k_factor == 3.0


def test_write_chunk_manifests(tmp_path: Path) -> None:
    embedder = EmbeddingBackend(model="fake", fake_mode=True)
    base_vector = np.arange(0, min(16, embedder.dim), dtype=float)
    if base_vector.size < embedder.dim:
        base_vector = np.pad(base_vector, (0, embedder.dim - base_vector.size))
    vector = base_vector.tolist()

    record = index_pipeline.ChunkRecord(
        chunk_id="chunk-1",
        normalized_id="entry-1",
        normalized_path="normalized/path.yaml",
        chunk_index=0,
        chunk_type="entry",
        chunk_text="Focus chunk",
        date="2024-01-02",
        tags=["focus"],
        source_type="markdown",
        source_path="data/entry.md",
        tokens=120,
        source_hash="source-hash",
        manifest_hash="manifest-hash",
    )
    record.embedding = vector

    chunk_dir = tmp_path / "chunks"
    index_pipeline.write_chunk_manifests(
        chunk_dir,
        {"2024-01-02": [record]},
        embedder,
    )

    artifact_path = chunk_dir / "2024-01-02.yaml"
    artifact = load_artifact(artifact_path, ChunkBatch)
    assert artifact.kind is ArtifactKind.INDEX_CHUNKS
    assert artifact.meta.model == embedder.model
    assert artifact.meta.notes
    assert artifact.meta.notes["vector_dimension"] == str(embedder.dim)
    assert artifact.data.day == "2024-01-02"
    assert len(artifact.data.chunks) == 1
    chunk = artifact.data.chunks[0]
    assert chunk.text == "Focus chunk"
    assert chunk.tags == ["focus"]
    assert chunk.date == "2024-01-02"

    vector_path = chunk_dir / "2024-01-02.npy"
    assert vector_path.exists()
    loaded_vectors = np.load(vector_path)
    assert loaded_vectors.shape[0] == 1


def test_token_estimate_defaults() -> None:
    assert index_pipeline.token_estimate("abcd", 0.0) == 1
    assert index_pipeline.token_estimate("a" * 50, 10.0) >= 1


def test_summary_chunk_records_build() -> None:
    day = "2025-11-15"
    summary = DailySummary(
        day=day,
        bullets=["Finished work on retrieval"],
        highlights=["Indexes now include summaries"],
        todo_candidates=["Document the new paths"],
    )
    records = index_pipeline._summary_chunk_records(
        day,
        summary,
        source_path="derived/summaries/2025-11-15.yaml",
        char_per_token=4.0,
    )
    assert len(records) == 3
    assert all(record.chunk_type == "summary" for record in records)
    assert records[0].chunk_id.startswith("summary-2025-11-15")


def test_microfact_chunk_records_build() -> None:
    day = "2025-11-15"
    microfacts = MicroFactsFile(
        facts=[
            MicroFact(
                id="fact-001",
                statement="Fact one",
                confidence=0.8,
                evidence=SourceRef(entry_id="entry-1"),
            ),
            MicroFact(
                id="fact-002",
                statement="Fact two",
                confidence=0.9,
                evidence=SourceRef(entry_id="entry-1"),
            ),
        ],
    )
    records = index_pipeline._microfact_chunk_records(
        day,
        microfacts,
        source_path="derived/microfacts/2025-11-15.yaml",
        char_per_token=4.0,
    )
    assert len(records) == 2
    assert all(record.chunk_type == "microfact" for record in records)
    assert records[0].chunk_id.endswith("#c0")


def test_index_entries_builds_summary_and_microfact_chunks(tmp_path: Path) -> None:
    day = "2025-11-15"
    derived = tmp_path / "derived"
    summary_path = derived / "summaries" / f"{day}.yaml"
    microfacts_path = derived / "microfacts" / f"{day}.yaml"
    summary = DailySummary(
        day=day,
        bullets=["First bullet"],
        highlights=["Key highlight"],
        todo_candidates=["Plan testing"],
    )
    timestamp = time_utils.format_timestamp(time_utils.now())
    save_artifact(
        summary_path,
        Artifact[DailySummary](
            kind=ArtifactKind.SUMMARY_DAILY,
            meta=ArtifactMeta(created_at=timestamp, model="fake"),
            data=summary,
        ),
    )
    microfacts = MicroFactsFile(
        facts=[
            MicroFact(
                id="fact-001",
                statement="Fact one",
                confidence=0.75,
                evidence=SourceRef(entry_id="entry-1"),
            ),
            MicroFact(
                id="fact-002",
                statement="Fact two",
                confidence=0.65,
                evidence=SourceRef(entry_id="entry-1"),
            ),
        ],
    )
    save_artifact(
        microfacts_path,
        Artifact[MicroFactsFile](
            kind=ArtifactKind.MICROFACTS_DAILY,
            meta=ArtifactMeta(created_at=timestamp, model="fake"),
            data=microfacts,
        ),
    )

    entry = NormalizedEntry(
        id="entry-1",
        created_at="2025-11-15T10:00:00Z",
        source_path="data/entry-1.md",
        title="Focus",
        summary="Focus work",
    )
    tasks = [
        index_pipeline.IndexTask(
            day=day,
            path=tmp_path / "normalized" / day / "entry-1.yaml",
            normalized_path="normalized/entry-1.yaml",
            normalized_id="entry-1",
            entry=entry,
            source_hash="hash-1",
            manifest=None,
        ),
    ]
    embedder = EmbeddingBackend(model="fake", fake_mode=True)
    chunk_index = FakeChunkIndex()
    stats, records_by_day = index_pipeline.index_entries(
        tasks,
        chunk_index,
        embedder,
        char_per_token=4.0,
        workspace=tmp_path,
        config=AppConfig(),
    )

    assert stats["summary_chunks"] == 3
    assert stats["microfact_chunks"] == 2
    day_records = records_by_day[day]
    summaries = [record for record in day_records if record.chunk_type == "summary"]
    microfact_records = [record for record in day_records if record.chunk_type == "microfact"]
    assert len(summaries) == 3
    assert len(microfact_records) == 2
