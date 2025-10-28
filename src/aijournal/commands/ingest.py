"""Orchestration helpers for the `aijournal ingest` command."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import typer
import yaml
from pydantic import ValidationError
from pydantic_ai import Agent

from aijournal.ingest_agent import (
    IngestResult,
    IngestSection,
    build_ingest_agent,
    ingest_with_agent,
)
from aijournal.models import ManifestEntry
from aijournal.pipelines import normalization
from aijournal.schema import SchemaValidationError, validate_schema
from aijournal.services import build_ollama_config_from_mapping
from aijournal.utils import time as time_utils
from aijournal.utils.paths import normalized_entry_path

MARKDOWN_SUFFIXES = {".md", ".markdown"}


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_config(root: Path) -> dict[str, Any]:
    config_path = root / "config" / "config.yaml"
    if not config_path.exists():
        return {}
    return _load_yaml(config_path)


def _use_fake_llm() -> bool:
    return os.getenv("AIJOURNAL_FAKE_OLLAMA") == "1"


def _manifest_path(root: Path) -> Path:
    return root / "data" / "manifest" / "ingested.yaml"


def _load_manifest(path: Path) -> list[ManifestEntry]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    entries: list[ManifestEntry] = []
    if not isinstance(data, list):
        return entries
    for raw in data:
        if not isinstance(raw, dict):
            continue
        try:
            entries.append(ManifestEntry.model_validate(raw))
        except ValidationError:
            continue
    return entries


def _write_manifest(path: Path, entries: Iterable[ManifestEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [entry.model_dump(mode="python") for entry in entries]
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _load_existing_yaml(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_yaml_if_changed(
    path: Path,
    data: dict[str, Any],
    *,
    schema: str | None = None,
) -> bool:
    if schema:
        try:
            validate_schema(schema, data)
        except SchemaValidationError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1)

    existing = _load_existing_yaml(path)
    if existing == data:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return True


def _split_frontmatter(text: str) -> tuple[str, str]:
    delimiter = None
    if text.startswith("---"):
        delimiter = "---"
    elif text.startswith("+++"):
        delimiter = "+++"
    if delimiter is None:
        msg = "Markdown entry missing YAML/TOML frontmatter delimiter"
        raise ValueError(msg)

    parts = text.split(delimiter, 2)
    if len(parts) < 3:
        msg = "Incomplete YAML/TOML frontmatter block"
        raise ValueError(msg)

    frontmatter_raw = parts[1].strip()
    body = parts[2]
    return frontmatter_raw, body


def _scan_headings(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for line in text.splitlines():
        heading_match = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if heading_match:
            sections.append(
                {
                    "heading": heading_match.group(2).strip(),
                    "level": len(heading_match.group(1)),
                },
            )
    return sections


def _parse_entry(entry_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    text = entry_path.read_text(encoding="utf-8")
    frontmatter_raw, body = _split_frontmatter(text)
    data = yaml.safe_load(frontmatter_raw) or {}
    sections = _scan_headings(body)
    return data, sections


def _relative_source_path(entry_path: Path, root: Path) -> str:
    try:
        return str(entry_path.relative_to(root))
    except ValueError:
        return str(entry_path)


def _extract_frontmatter_tags(frontmatter: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("tags", "categories", "keywords", "topics", "projects"):
        raw = frontmatter.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, list):
            for item in raw:
                values.append(str(item))
    return values


def _parse_datetime(value: str) -> datetime | None:
    try:
        candidate = value.replace("Z", "+00:00") if value.endswith("Z") else value
        dt = datetime.fromisoformat(candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def _fake_structured_entry(entry_path: Path) -> IngestResult:
    try:
        frontmatter, sections_raw = _parse_entry(entry_path)
    except ValueError:
        frontmatter = {}
        sections_raw = []

    created_value = (
        frontmatter.get("created_at")
        or frontmatter.get("date")
        or frontmatter.get("published")
        or time_utils.format_timestamp(time_utils.now())
    )
    created_dt = _parse_datetime(str(created_value)) or time_utils.now()
    title = str(frontmatter.get("title") or entry_path.stem)
    section_models = [
        IngestSection(
            heading=str(section.get("heading", title)),
            level=int(section.get("level", 2) or 2),
        )
        for section in sections_raw
    ]
    if not section_models:
        section_models = [IngestSection(heading=title, level=1)]

    summary = frontmatter.get("summary")
    tags = _extract_frontmatter_tags(frontmatter)
    entry_id = frontmatter.get("id") or frontmatter.get("slug")

    return IngestResult(
        entry_id=str(entry_id) if entry_id else None,
        created_at=created_dt,
        title=title,
        tags=tags,
        sections=section_models,
        summary=str(summary) if isinstance(summary, str) else None,
    )


def _normalized_from_structured(
    structured: IngestResult,
    *,
    source_path: Path,
    root: Path,
    digest: str,
    source_type: str,
    fallback_sections: list[dict[str, Any]] | None = None,
    fallback_tags: list[str] | None = None,
    fallback_summary: str | None = None,
) -> tuple[dict[str, Any], str]:
    normalized, date_str = normalization.normalized_from_structured(
        structured,
        source_path=_relative_source_path(source_path, root),
        root=root,
        digest=digest,
        source_type=source_type,
        fallback_sections=fallback_sections,
        fallback_tags=fallback_tags,
        fallback_summary=fallback_summary,
    )
    return normalized, date_str


def _discover_markdown_files(inputs: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for source in inputs:
        resolved = source.expanduser().resolve()
        if resolved.is_dir():
            for candidate in sorted(resolved.rglob("*")):
                if candidate.is_file() and candidate.suffix.lower() in MARKDOWN_SUFFIXES:
                    files.append(candidate)
        elif resolved.is_file():
            files.append(resolved)

    unique: list[Path] = []
    seen: set[Path] = set()
    for file in files:
        if file not in seen:
            seen.add(file)
            unique.append(file)
    return unique


def run_ingest(
    sources: list[Path],
    *,
    source_type: str,
    limit: int | None,
    snapshot: bool,
) -> None:
    """Ingest Markdown files into normalized YAML entries."""
    if limit is not None and limit <= 0:
        typer.secho("--limit must be positive when provided.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    root = Path.cwd()
    files = _discover_markdown_files(sources)
    if not files:
        typer.secho(
            "No Markdown files found in the provided sources.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    if limit is not None:
        files = files[:limit]

    config = _load_config(root)
    llm_config = build_ollama_config_from_mapping(config)
    model_name = llm_config.model
    is_fake = _use_fake_llm()
    agent: Agent | None = None
    if not is_fake:
        try:
            agent = build_ingest_agent(config, model=model_name)
        except Exception as exc:  # pragma: no cover - initialization errors are rare
            typer.secho(
                f"Unable to initialize Ollama ingestion agent: {exc}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1)

    manifest_path = _manifest_path(root)
    manifest_entries = _load_manifest(manifest_path)
    known_hashes = {entry.hash: entry for entry in manifest_entries if entry.hash}

    ingested = 0
    skipped = 0
    errors = 0
    raw_dir = root / "data" / "raw"

    for file in files:
        try:
            raw_bytes = file.read_bytes()
        except OSError as exc:
            errors += 1
            typer.secho(f"Failed to read {file}: {exc}", fg=typer.colors.RED, err=True)
            continue

        digest = sha256(raw_bytes).hexdigest()
        if digest in known_hashes:
            skipped += 1
            typer.echo(f"Skipping {file} (already ingested)")
            continue

        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            errors += 1
            typer.secho(f"Failed to decode {file}: {exc}", fg=typer.colors.RED, err=True)
            continue

        try:
            frontmatter_data, fallback_sections = _parse_entry(file)
        except ValueError:
            frontmatter_data = {}
            fallback_sections = _scan_headings(text)
        fallback_tags = _extract_frontmatter_tags(frontmatter_data)
        fallback_summary = frontmatter_data.get("summary")
        if fallback_summary is not None:
            fallback_summary = str(fallback_summary)

        try:
            if is_fake:
                structured = _fake_structured_entry(file)
            else:
                assert agent is not None
                structured = ingest_with_agent(agent, source_path=file, markdown=text)
            normalized, date_str = _normalized_from_structured(
                structured,
                source_path=file,
                root=root,
                digest=digest,
                source_type=source_type,
                fallback_sections=fallback_sections,
                fallback_tags=fallback_tags,
                fallback_summary=fallback_summary,
            )
        except Exception as exc:
            errors += 1
            typer.secho(f"Failed to ingest {file}: {exc}", fg=typer.colors.RED, err=True)
            continue

        normalized_path = normalized_entry_path(root, date_str, normalized["id"])
        _write_yaml_if_changed(
            normalized_path,
            normalized,
            schema="normalized_entry",
        )

        if snapshot:
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"{digest}.md").write_bytes(raw_bytes)

        manifest_entry = ManifestEntry(
            hash=digest,
            path=_relative_source_path(file, root),
            normalized=_relative_source_path(normalized_path, root),
            source_type=source_type,
            ingested_at=time_utils.format_timestamp(time_utils.now()),
            created_at=str(normalized["created_at"]),
            id=str(normalized["id"]),
            tags=list(normalized.get("tags", [])),
            model=model_name if not is_fake else "fake-ollama",
        )
        manifest_entries.append(manifest_entry)
        known_hashes[digest] = manifest_entry

        typer.echo(f"Ingested {file} -> {normalized_path}")
        ingested += 1

    if ingested:
        _write_manifest(manifest_path, manifest_entries)

    typer.echo(f"Ingest summary: {ingested} new, {skipped} skipped, {errors} errors.")
    if errors:
        raise typer.Exit(1)
