"""Index command orchestration helpers."""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import typer

from aijournal.commands.facts import _manifest_by_id
from aijournal.commands.ingest import (
    _load_config,
    _load_manifest,
    _manifest_path,
    _relative_source_path,
    _use_fake_llm,
)
from aijournal.pipelines import index as index_pipeline
from aijournal.services.embedding import EmbeddingBackend
from aijournal.services.retriever import RetrievalFilters, Retriever
from aijournal.utils import time as time_utils

INDEX_DB_FILENAME = "index.db"
ANNOY_FILENAME = "annoy.index"
INDEX_META_FILENAME = "meta.json"


def run_index_rebuild(
    since: str | None,
    *,
    limit: int | None,
) -> str:
    """Rebuild the Annoy + SQLite retrieval index."""
    root = Path.cwd()
    if limit is not None and limit <= 0:
        typer.secho("--limit must be positive when provided.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    since_filter = _resolve_since_filter(since)
    entries = _collect_normalized_files(root, since_filter)
    if limit is not None:
        entries = entries[:limit]
    if not entries:
        typer.secho(
            "No normalized entries available for indexing.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    manifest_index = _manifest_by_id(_load_manifest(_manifest_path(root)))
    tasks = index_pipeline.prepare_index_tasks(
        entries,
        root=root,
        manifest_index=manifest_index,
        relative_path=lambda entry_path: _relative_source_path(entry_path, root),
    )
    if not tasks:
        typer.secho("No normalized entries with valid IDs found.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    config = _load_config(root)
    embedder = _build_embedding_backend(config)
    ann_trees, search_k_factor, char_per_token = _index_settings(config)

    db_path = _index_db_path(root)
    index_dir = _index_dir(root)
    index_dir.mkdir(parents=True, exist_ok=True)
    conn = _connect_index_db(db_path, overwrite=True)
    with conn:
        _prepare_index_schema(conn)
        stats = index_pipeline.index_entries(conn, tasks, embedder, char_per_token)

    chunk_total, entry_total = index_pipeline.gather_index_stats(conn)
    index_pipeline.rebuild_annoy_index(conn, embedder.dim, ann_trees, _annoy_index_path(root))
    conn.commit()
    if stats["dates"]:
        index_pipeline.write_chunk_manifests(
            conn,
            _chunk_manifest_dir(root),
            stats["dates"],
            embedder,
        )
    conn.close()

    index_pipeline.write_index_meta(
        root,
        embedder=embedder,
        chunk_total=chunk_total,
        entry_total=entry_total,
        mode="rebuild",
        fake_mode=_use_fake_llm(),
        ann_trees=ann_trees,
        search_k_factor=search_k_factor,
        char_per_token=char_per_token,
        since=since_filter,
        limit=limit,
        touched_dates=stats["dates"],
        index_meta_path=_index_meta_path,
    )

    return f"Indexed {chunk_total} chunks across {entry_total} entries (mode: rebuild)."


def run_index_tail(
    since: str | None,
    *,
    days: int,
    limit: int | None,
) -> str:
    """Tail the retrieval index by ingesting recently normalized entries."""
    if days <= 0:
        typer.secho("--days must be positive.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if limit is not None and limit <= 0:
        typer.secho("--limit must be positive when provided.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    root = Path.cwd()
    db_path = _index_db_path(root)
    if not db_path.exists():
        typer.secho(
            "Index database not found. Run `aijournal index rebuild` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    since_filter = _resolve_since_filter(since, fallback_days=days)
    entries = _collect_normalized_files(root, since_filter)
    if limit is not None:
        entries = entries[:limit]
    if not entries:
        typer.secho(
            "No normalized entries matched the requested window.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    manifest_index = _manifest_by_id(_load_manifest(_manifest_path(root)))
    tasks = index_pipeline.prepare_index_tasks(
        entries,
        root=root,
        manifest_index=manifest_index,
        relative_path=lambda entry_path: _relative_source_path(entry_path, root),
    )
    conn = _connect_index_db(db_path)
    try:
        pending = index_pipeline.filter_tasks_for_tail(conn, tasks)
        if not pending:
            return "Index already up to date for requested window."

        config = _load_config(root)
        embedder = _build_embedding_backend(config)
        ann_trees, search_k_factor, char_per_token = _index_settings(config)

        with conn:
            _prepare_index_schema(conn)
            stats = index_pipeline.index_entries(conn, pending, embedder, char_per_token)

        chunk_total, entry_total = index_pipeline.gather_index_stats(conn)
        index_pipeline.rebuild_annoy_index(conn, embedder.dim, ann_trees, _annoy_index_path(root))
        conn.commit()
        if stats["dates"]:
            index_pipeline.write_chunk_manifests(
                conn,
                _chunk_manifest_dir(root),
                stats["dates"],
                embedder,
            )

        index_pipeline.write_index_meta(
            root,
            embedder=embedder,
            chunk_total=chunk_total,
            entry_total=entry_total,
            mode="tail",
            fake_mode=_use_fake_llm(),
            ann_trees=ann_trees,
            search_k_factor=search_k_factor,
            char_per_token=char_per_token,
            since=since_filter,
            limit=limit,
            touched_dates=stats["dates"],
            index_meta_path=_index_meta_path,
        )

        return f"Indexed {stats['chunks']} chunks across {stats['entries']} entries (mode: tail)."
    finally:
        conn.close()


def run_index_search(
    query: str,
    *,
    top: int,
    tags: str | None,
    source: str | None,
    date_from: str | None,
    date_to: str | None,
) -> None:
    """Search the retrieval index and display formatted results."""
    if top <= 0:
        typer.secho("--top must be positive.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    filters = RetrievalFilters(
        tags=_split_filter_values(tags),
        source_types=_split_filter_values(source),
        date_from=_validate_date_option(date_from, "--date-from"),
        date_to=_validate_date_option(date_to, "--date-to"),
    )

    root = Path.cwd()
    config = _load_config(root)
    retriever = Retriever(root, config)
    try:
        result = retriever.search(query, k=top, filters=filters)
    except (RuntimeError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    finally:
        retriever.close()

    if not result.chunks:
        typer.echo("No matches found.")
        return

    header = f"Top {len(result.chunks)} match(es) - source: {result.meta.source}"
    if result.meta.fake_mode:
        header += " (fake mode)"
    typer.echo(header)

    for idx, chunk in enumerate(result.chunks, start=1):
        tag_display = ", ".join(chunk.tags) if chunk.tags else "-"
        source_path = chunk.source_path or chunk.normalized_id
        snippet = _format_search_snippet(chunk.text)
        typer.echo(f"{idx}. [{chunk.date}] {source_path}")
        typer.echo(f"   score: {chunk.score:.3f}  tags: {tag_display}")
        typer.echo(f"   {snippet}")
        if idx != len(result.chunks):
            typer.echo("")


def _index_dir(root: Path) -> Path:
    return root / "derived" / "index"


def _index_db_path(root: Path) -> Path:
    return _index_dir(root) / INDEX_DB_FILENAME


def _annoy_index_path(root: Path) -> Path:
    return _index_dir(root) / ANNOY_FILENAME


def _chunk_manifest_dir(root: Path) -> Path:
    return _index_dir(root) / "chunks"


def _index_meta_path(root: Path) -> Path:
    return _index_dir(root) / INDEX_META_FILENAME


def _collect_normalized_files(root: Path, since: str | None) -> list[tuple[str, Path]]:
    normalized_root = root / "data" / "normalized"
    if not normalized_root.exists():
        return []
    entries: list[tuple[str, Path]] = []
    for day_dir in sorted(p for p in normalized_root.iterdir() if p.is_dir()):
        day = day_dir.name
        if since and day < since:
            continue
        for file in sorted(day_dir.glob("*.yaml")):
            entries.append((day, file))
    return entries


def _resolve_since_filter(value: str | None, fallback_days: int | None = None) -> str | None:
    if value:
        text = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
        if text.endswith("d") and text[:-1].isdigit():
            window = int(text[:-1])
            return (time_utils.now() - timedelta(days=window)).strftime("%Y-%m-%d")
        typer.secho(
            "--since must be YYYY-MM-DD or Nd (e.g., 7d)",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    if fallback_days is not None:
        return (time_utils.now() - timedelta(days=fallback_days)).strftime("%Y-%m-%d")
    return None


def _validate_date_option(value: str | None, option: str) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        typer.secho(f"{option} must be YYYY-MM-DD.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    return text


def _split_filter_values(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    parts = [part.strip() for part in re.split(r"[,\s]+", raw) if part.strip()]
    return frozenset(parts)


def _format_search_snippet(text: str, limit: int = 200) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."


def _build_embedding_backend(config: dict[str, Any]) -> EmbeddingBackend:
    model = str(config.get("embedding_model") or "embeddinggemma")
    host = os.getenv("AIJOURNAL_OLLAMA_HOST")
    return EmbeddingBackend(model, host=host, fake_mode=_use_fake_llm())


def _index_settings(config: dict[str, Any]) -> tuple[int, float, float]:
    index_cfg_raw = config.get("index")
    index_cfg = index_cfg_raw if isinstance(index_cfg_raw, dict) else {}
    ann_trees = int(index_cfg.get("ann_trees") or 50)
    search_k_factor = float(index_cfg.get("search_k_factor") or 3.0)
    token_cfg_raw = config.get("token_estimator")
    token_cfg = token_cfg_raw if isinstance(token_cfg_raw, dict) else {}
    char_per_token = float(token_cfg.get("char_per_token") or 4.2)
    return ann_trees, search_k_factor, char_per_token


def _connect_index_db(path: Path, *, overwrite: bool = False) -> sqlite3.Connection:
    if overwrite and path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _prepare_index_schema(conn: sqlite3.Connection) -> None:
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chunks (
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

            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts
            USING fts5(
                chunk_id UNINDEXED,
                chunk_text,
                content=''
            );

            CREATE TABLE IF NOT EXISTS sources (
                normalized_path TEXT PRIMARY KEY,
                normalized_id TEXT NOT NULL,
                date TEXT NOT NULL,
                source_hash TEXT,
                manifest_hash TEXT,
                chunk_count INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS annoy_map (
                annoy_idx INTEGER PRIMARY KEY,
                chunk_id TEXT NOT NULL
            );
            """,
        )
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "fts5" in message:
            msg = (
                "SQLite runtime does not support FTS5, which is required for the retrieval index. "
                "Install a Python build with FTS5 enabled (e.g., the system sqlite3 on macOS via Homebrew) "
                "or rebuild Python against an FTS5-capable SQLite."
            )
            raise RuntimeError(msg) from exc
        raise
