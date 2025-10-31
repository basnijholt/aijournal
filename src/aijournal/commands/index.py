"""Index command orchestration helpers."""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import typer
from pydantic import BaseModel

from aijournal.commands.facts import _manifest_by_id
from aijournal.commands.ingest import (
    _load_config,
    _load_manifest,
    _manifest_path,
    _relative_source_path,
    _use_fake_llm,
)
from aijournal.common.app_config import AppConfig
from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.context import RunContext, create_run_context
from aijournal.pipelines import index as index_pipeline
from aijournal.services.embedding import EmbeddingBackend
from aijournal.services.retriever import RetrievalFilters, RetrievalResult, Retriever
from aijournal.utils import time as time_utils
from aijournal.utils.paths import WorkspacePaths

INDEX_DB_FILENAME = "index.db"
ANNOY_FILENAME = "annoy.index"
INDEX_META_FILENAME = "meta.json"


class IndexRebuildOptions(BaseModel):
    since: str | None = None
    limit: int | None = None


@dataclass(slots=True)
class IndexRebuildPrepared:
    tasks: list[index_pipeline.IndexTask]
    config: AppConfig
    since_filter: str | None
    limit: int | None
    entries_considered: int


@dataclass(slots=True)
class IndexRebuildResult:
    message: str
    chunks: int
    entries: int
    touched_dates: list[str]


class IndexTailOptions(BaseModel):
    since: str | None = None
    days: int = 7
    limit: int | None = None


@dataclass(slots=True)
class IndexTailPrepared:
    tasks: list[index_pipeline.IndexTask]
    config: AppConfig
    since_filter: str | None
    limit: int | None
    days: int


@dataclass(slots=True)
class IndexTailResult:
    message: str
    chunks: int
    entries: int
    touched_dates: list[str]
    up_to_date: bool = False


class IndexSearchOptions(BaseModel):
    query: str
    top: int
    tags: str | None = None
    source: str | None = None
    date_from: str | None = None
    date_to: str | None = None


@dataclass(slots=True)
class IndexSearchPrepared:
    query: str
    top: int
    filters: RetrievalFilters


@dataclass(slots=True)
class IndexSearchResult:
    result: RetrievalResult


def run_index_rebuild(since: str | None, *, limit: int | None) -> str:
    """Rebuild the Annoy + SQLite retrieval index."""

    root = Path.cwd()
    config = _load_config(root)
    ctx = create_run_context(
        command="index.rebuild",
        workspace=root,
        config=config,
        use_fake_llm=_use_fake_llm(),
        trace=False,
        verbose_json=False,
    )
    options = IndexRebuildOptions(since=since, limit=limit)
    return run_index_rebuild_command(ctx, options)


def run_index_tail(since: str | None, *, days: int, limit: int | None) -> str:
    """Tail the retrieval index by ingesting recently normalized entries."""

    root = Path.cwd()
    config = _load_config(root)
    ctx = create_run_context(
        command="index.update",
        workspace=root,
        config=config,
        use_fake_llm=_use_fake_llm(),
        trace=False,
        verbose_json=False,
    )
    options = IndexTailOptions(since=since, days=days, limit=limit)
    return run_index_tail_command(ctx, options)


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

    root = Path.cwd()
    config = _load_config(root)
    ctx = create_run_context(
        command="index.search",
        workspace=root,
        config=config,
        use_fake_llm=_use_fake_llm(),
        trace=False,
        verbose_json=False,
    )
    options = IndexSearchOptions(
        query=query,
        top=top,
        tags=tags,
        source=source,
        date_from=date_from,
        date_to=date_to,
    )
    run_index_search_command(ctx, options)


def run_index_rebuild_command(ctx: RunContext, options: IndexRebuildOptions) -> str:
    return run_command_pipeline(
        ctx,
        options,
        prepare_inputs=_prepare_rebuild_inputs,
        invoke_pipeline=_invoke_rebuild_pipeline,
        persist_output=_persist_rebuild_output,
    )


def run_index_tail_command(ctx: RunContext, options: IndexTailOptions) -> str:
    return run_command_pipeline(
        ctx,
        options,
        prepare_inputs=_prepare_tail_inputs,
        invoke_pipeline=_invoke_tail_pipeline,
        persist_output=_persist_tail_output,
    )


def run_index_search_command(ctx: RunContext, options: IndexSearchOptions) -> None:
    run_command_pipeline(
        ctx,
        options,
        prepare_inputs=_prepare_search_inputs,
        invoke_pipeline=_invoke_search_pipeline,
        persist_output=_persist_search_output,
    )


def _prepare_rebuild_inputs(ctx: RunContext, options: IndexRebuildOptions) -> IndexRebuildPrepared:
    if options.limit is not None and options.limit <= 0:
        typer.secho("--limit must be positive when provided.", fg=typer.colors.RED, err=True)
        ctx.emit(event="invalid_option", option="limit")
        raise typer.Exit(1)

    since_filter = _resolve_since_filter(options.since)
    entries = _collect_normalized_files(ctx.root, since_filter)
    if options.limit is not None:
        entries = entries[: options.limit]
    if not entries:
        typer.secho(
            "No normalized entries available for indexing.",
            fg=typer.colors.RED,
            err=True,
        )
        ctx.emit(event="no_entries")
        raise typer.Exit(1)

    manifest_index = _manifest_by_id(_load_manifest(_manifest_path(ctx.root)))
    tasks = index_pipeline.prepare_index_tasks(
        entries,
        root=ctx.root,
        manifest_index=manifest_index,
        relative_path=lambda entry_path: _relative_source_path(entry_path, ctx.root),
    )
    if not tasks:
        typer.secho("No normalized entries with valid IDs found.", fg=typer.colors.RED, err=True)
        ctx.emit(event="no_tasks")
        raise typer.Exit(1)

    config = _load_config(ctx.root)
    ctx.emit(
        event="prepare_index",
        entries=len(entries),
        tasks=len(tasks),
        since=since_filter,
    )
    return IndexRebuildPrepared(
        tasks=list(tasks),
        config=config,
        since_filter=since_filter,
        limit=options.limit,
        entries_considered=len(entries),
    )


def _invoke_rebuild_pipeline(ctx: RunContext, prepared: IndexRebuildPrepared) -> IndexRebuildResult:
    embedder = _build_embedding_backend(prepared.config)
    ann_trees, search_k_factor, char_per_token = _index_settings(prepared.config)

    stats: dict[str, Any] = {"entries": 0, "chunks": 0, "dates": []}
    chunk_total = 0
    entry_total = 0
    touched_dates: list[str] = []

    index_dir = _index_dir(ctx.root)
    index_dir.mkdir(parents=True, exist_ok=True)
    conn = _connect_index_db(_index_db_path(ctx.root), overwrite=True)
    try:
        with conn:
            _prepare_index_schema(conn)
            stats = index_pipeline.index_entries(
                conn,
                prepared.tasks,
                embedder,
                char_per_token,
            )
        chunk_total, entry_total = index_pipeline.gather_index_stats(conn)
        index_pipeline.rebuild_annoy_index(
            conn,
            embedder.dim,
            ann_trees,
            _annoy_index_path(ctx.root),
        )
        conn.commit()
        touched_dates = sorted(stats.get("dates", []))
        if touched_dates:
            index_pipeline.write_chunk_manifests(
                conn,
                _chunk_manifest_dir(ctx.root),
                touched_dates,
                embedder,
            )
    finally:
        conn.close()

    index_pipeline.write_index_meta(
        ctx.root,
        embedder=embedder,
        chunk_total=chunk_total,
        entry_total=entry_total,
        mode="rebuild",
        fake_mode=_use_fake_llm(),
        ann_trees=ann_trees,
        search_k_factor=search_k_factor,
        char_per_token=char_per_token,
        since=prepared.since_filter,
        limit=prepared.limit,
        touched_dates=touched_dates,
        index_meta_path=_index_meta_path,
    )

    message = f"Indexed {chunk_total} chunks across {entry_total} entries (mode: rebuild)."
    ctx.emit(
        event="index_rebuild_complete",
        chunks=chunk_total,
        entries=entry_total,
        dates=touched_dates,
    )
    return IndexRebuildResult(
        message=message,
        chunks=chunk_total,
        entries=entry_total,
        touched_dates=touched_dates,
    )


def _persist_rebuild_output(ctx: RunContext, result: IndexRebuildResult) -> str:
    ctx.emit(
        event="persist_complete",
        message=result.message,
        chunks=result.chunks,
        entries=result.entries,
    )
    return result.message


def _prepare_tail_inputs(ctx: RunContext, options: IndexTailOptions) -> IndexTailPrepared:
    if options.days <= 0:
        typer.secho("--days must be positive.", fg=typer.colors.RED, err=True)
        ctx.emit(event="invalid_option", option="days")
        raise typer.Exit(1)
    if options.limit is not None and options.limit <= 0:
        typer.secho("--limit must be positive when provided.", fg=typer.colors.RED, err=True)
        ctx.emit(event="invalid_option", option="limit")
        raise typer.Exit(1)

    db_path = _index_db_path(ctx.root)
    if not db_path.exists():
        typer.secho(
            "Index database not found. Run `aijournal index rebuild` first.",
            fg=typer.colors.RED,
            err=True,
        )
        ctx.emit(event="missing_index_db", path=str(db_path))
        raise typer.Exit(1)

    since_filter = _resolve_since_filter(options.since, fallback_days=options.days)
    entries = _collect_normalized_files(ctx.root, since_filter)
    if options.limit is not None:
        entries = entries[: options.limit]
    if not entries:
        typer.secho(
            "No normalized entries matched the requested window.",
            fg=typer.colors.RED,
            err=True,
        )
        ctx.emit(event="no_entries")
        raise typer.Exit(1)

    manifest_index = _manifest_by_id(_load_manifest(_manifest_path(ctx.root)))
    tasks = index_pipeline.prepare_index_tasks(
        entries,
        root=ctx.root,
        manifest_index=manifest_index,
        relative_path=lambda entry_path: _relative_source_path(entry_path, ctx.root),
    )
    config = _load_config(ctx.root)
    ctx.emit(
        event="prepare_tail",
        entries=len(entries),
        tasks=len(tasks),
        since=since_filter,
    )
    return IndexTailPrepared(
        tasks=list(tasks),
        config=config,
        since_filter=since_filter,
        limit=options.limit,
        days=options.days,
    )


def _invoke_tail_pipeline(ctx: RunContext, prepared: IndexTailPrepared) -> IndexTailResult:
    db_path = _index_db_path(ctx.root)
    conn = _connect_index_db(db_path)
    try:
        pending = index_pipeline.filter_tasks_for_tail(conn, prepared.tasks)
        if not pending:
            ctx.emit(event="index_up_to_date")
            return IndexTailResult(
                message="Index already up to date for requested window.",
                chunks=0,
                entries=0,
                touched_dates=[],
                up_to_date=True,
            )

        embedder = _build_embedding_backend(prepared.config)
        ann_trees, search_k_factor, char_per_token = _index_settings(prepared.config)

        stats: dict[str, Any] = {"entries": 0, "chunks": 0, "dates": []}
        with conn:
            _prepare_index_schema(conn)
            stats = index_pipeline.index_entries(conn, pending, embedder, char_per_token)

        chunk_total, entry_total = index_pipeline.gather_index_stats(conn)
        index_pipeline.rebuild_annoy_index(
            conn,
            embedder.dim,
            ann_trees,
            _annoy_index_path(ctx.root),
        )
        conn.commit()
        touched_dates = sorted(stats.get("dates", []))
        if touched_dates:
            index_pipeline.write_chunk_manifests(
                conn,
                _chunk_manifest_dir(ctx.root),
                touched_dates,
                embedder,
            )

        index_pipeline.write_index_meta(
            ctx.root,
            embedder=embedder,
            chunk_total=chunk_total,
            entry_total=entry_total,
            mode="tail",
            fake_mode=_use_fake_llm(),
            ann_trees=ann_trees,
            search_k_factor=search_k_factor,
            char_per_token=char_per_token,
            since=prepared.since_filter,
            limit=prepared.limit,
            touched_dates=touched_dates,
            index_meta_path=_index_meta_path,
        )

        message = (
            f"Indexed {stats['chunks']} chunks across {stats['entries']} entries (mode: tail)."
        )
        ctx.emit(
            event="index_tail_complete",
            chunks=stats.get("chunks", 0),
            entries=stats.get("entries", 0),
            dates=touched_dates,
        )
        return IndexTailResult(
            message=message,
            chunks=int(stats.get("chunks", 0)),
            entries=int(stats.get("entries", 0)),
            touched_dates=touched_dates,
        )
    finally:
        conn.close()


def _persist_tail_output(ctx: RunContext, result: IndexTailResult) -> str:
    ctx.emit(
        event="persist_complete",
        message=result.message,
        chunks=result.chunks,
        entries=result.entries,
        up_to_date=result.up_to_date,
    )
    return result.message


def _prepare_search_inputs(ctx: RunContext, options: IndexSearchOptions) -> IndexSearchPrepared:
    if options.top <= 0:
        typer.secho("--top must be positive.", fg=typer.colors.RED, err=True)
        ctx.emit(event="invalid_option", option="top")
        raise typer.Exit(1)

    filters = RetrievalFilters(
        tags=_split_filter_values(options.tags),
        source_types=_split_filter_values(options.source),
        date_from=_validate_date_option(options.date_from, "--date-from"),
        date_to=_validate_date_option(options.date_to, "--date-to"),
    )
    ctx.emit(
        event="prepare_search",
        top=options.top,
        filters=filters.model_dump(mode="json"),
    )
    return IndexSearchPrepared(query=options.query, top=options.top, filters=filters)


def _invoke_search_pipeline(ctx: RunContext, prepared: IndexSearchPrepared) -> IndexSearchResult:
    retriever = Retriever(ctx.root, ctx.config)
    try:
        result = retriever.search(prepared.query, k=prepared.top, filters=prepared.filters)
    except (RuntimeError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        ctx.emit(event="search_error", error=str(exc))
        raise typer.Exit(1) from exc
    finally:
        retriever.close()

    ctx.emit(
        event="search_complete",
        results=len(result.chunks),
        fake_mode=getattr(result.meta, "fake_mode", False),
    )
    return IndexSearchResult(result=result)


def _persist_search_output(ctx: RunContext, search_result: IndexSearchResult) -> None:
    result = search_result.result
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
    del root  # Use WorkspacePaths instead
    return WorkspacePaths.derived() / "index"


def _index_db_path(root: Path) -> Path:
    return _index_dir(root) / INDEX_DB_FILENAME


def _annoy_index_path(root: Path) -> Path:
    return _index_dir(root) / ANNOY_FILENAME


def _chunk_manifest_dir(root: Path) -> Path:
    return _index_dir(root) / "chunks"


def _index_meta_path(root: Path) -> Path:
    return _index_dir(root) / INDEX_META_FILENAME


def _collect_normalized_files(root: Path, since: str | None) -> list[tuple[str, Path]]:
    del root  # Use WorkspacePaths instead
    normalized_root = WorkspacePaths.data() / "normalized"
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


def _build_embedding_backend(config: AppConfig) -> EmbeddingBackend:
    model = str(config.embedding_model or "embeddinggemma")
    host = os.getenv("AIJOURNAL_OLLAMA_HOST")
    return EmbeddingBackend(model, host=host, fake_mode=_use_fake_llm())


def _index_settings(config: AppConfig) -> tuple[int, float, float]:
    ann_trees = config.index.ann_trees
    search_k_factor = config.index.search_k_factor
    char_per_token = config.token_estimator.char_per_token
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
