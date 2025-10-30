from __future__ import annotations

import sqlite3
from pathlib import Path

from aijournal.domain.index import IndexMeta
from aijournal.io import load_artifact_data
from aijournal.io.yaml_io import write_yaml_model
from aijournal.models import ManifestEntry, NormalizedEntry
from aijournal.pipelines import index as index_pipeline
from aijournal.services.embedding import EmbeddingBackend


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


def _prepare_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            normalized_id TEXT NOT NULL,
            normalized_path TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            date TEXT NOT NULL,
            tags TEXT NOT NULL,
            source_type TEXT,
            source_path TEXT,
            tokens INTEGER NOT NULL,
            source_hash TEXT,
            manifest_hash TEXT,
            embedding BLOB NOT NULL
        );
        CREATE VIRTUAL TABLE chunk_fts USING fts5(chunk_id UNINDEXED, chunk_text, content='');
        CREATE TABLE sources (
            normalized_path TEXT PRIMARY KEY,
            normalized_id TEXT NOT NULL,
            date TEXT NOT NULL,
            source_hash TEXT,
            manifest_hash TEXT,
            chunk_count INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE annoy_map (
            annoy_idx INTEGER PRIMARY KEY,
            chunk_id TEXT NOT NULL
        );
        """
    )


def test_index_entries_and_annoy(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _prepare_schema(conn)

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
        )
    ]

    embedder = EmbeddingBackend(model="fake", fake_mode=True)
    stats = index_pipeline.index_entries(conn, tasks, embedder, char_per_token=4.0)
    assert stats["entries"] == 1

    chunk_total, entry_total = index_pipeline.gather_index_stats(conn)
    assert chunk_total == 1
    assert entry_total == 1

    index_path = tmp_path / "annoy.index"
    index_pipeline.rebuild_annoy_index(conn, embedder.dim, ann_trees=2, output_path=index_path)
    assert index_path.exists()


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
        ann_trees=10,
        search_k_factor=3.0,
        char_per_token=4.2,
        since="2024-01-01",
        limit=None,
        touched_dates={"2024-01-02"},
        index_meta_path=lambda base: meta_path,
    )

    meta = load_artifact_data(meta_path, IndexMeta)
    assert meta.chunk_count == 10
    assert meta.annoy_trees == 10


def test_token_estimate_defaults() -> None:
    assert index_pipeline.token_estimate("abcd", 0.0) == 1
    assert index_pipeline.token_estimate("a" * 50, 10.0) >= 1
