from __future__ import annotations

import re
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from aijournal.api.capture import CaptureInput
from aijournal.commands.ingest import _fake_structured_entry
from aijournal.common.config_loader import load_config, use_fake_llm
from aijournal.domain.journal import NormalizedEntry
from aijournal.ingest_agent import IngestResult, build_ingest_agent, ingest_with_agent
from aijournal.io.yaml_io import dump_yaml
from aijournal.models.authoritative import ManifestEntry
from aijournal.pipelines import normalization
from aijournal.services.capture.tolerant import (
    infer_created_at_from_context,
    split_frontmatter_tolerant,
)
from aijournal.services.capture.utils import (
    coerce_frontmatter_tags,
    digest_bytes,
    digest_text,
    ensure_manifest,
    ensure_unique_slug,
    journal_path,
    normalize_markdown,
    relative_path,
    resolve_created_dt,
    scan_headings,
    split_frontmatter,
    write_manifest,
    write_markdown_entry,
    write_snapshot,
    write_yaml_if_changed,
)
from aijournal.services.capture.utils import manifest_index as _manifest_index
from aijournal.services.capture.utils import manifest_path as _manifest_path
from aijournal.utils import time as time_utils
from aijournal.utils.paths import normalized_entry_path

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from aijournal.api.capture import CaptureInput
    from aijournal.common.app_config import AppConfig
    from aijournal.services.capture import PersistStage0Outputs


SUMMARY_CHAR_LIMIT = 400
SUMMARY_ELLIPSIS = "..."


def _first_paragraph(text: str) -> str:
    """Return the first non-empty paragraph from the Markdown body."""
    normalized = text.strip()
    if not normalized:
        return ""
    # Split on blank lines (optionally containing whitespace).
    paragraphs = [
        segment.strip() for segment in re.split(r"\n\s*\n", normalized) if segment.strip()
    ]
    return paragraphs[0] if paragraphs else normalized


def _truncate_to_word_boundary(text: str, max_chars: int) -> tuple[str, bool]:
    """Trim text to <= max_chars, preferring word boundaries."""
    if len(text) <= max_chars:
        return text, False
    cutoff = text.rfind(" ", 0, max_chars)
    if cutoff == -1 or cutoff < max_chars // 2:
        cutoff = max_chars
    trimmed = text[:cutoff].rstrip()
    return trimmed, True


def _derive_summary_text(
    existing_summary: Any | None,
    body: str,
    max_chars: int = SUMMARY_CHAR_LIMIT,
) -> str | None:
    """Return a deterministic summary for entries lacking one."""
    if existing_summary and existing_summary.strip():
        return str(existing_summary)

    first_paragraph = " ".join(_first_paragraph(body).split())
    if not first_paragraph:
        return None

    trimmed, truncated = _truncate_to_word_boundary(first_paragraph, max_chars)
    return f"{trimmed}{SUMMARY_ELLIPSIS}" if truncated else trimmed


def _ingest_frontmatter(
    inputs: CaptureInput,
    *,
    root: Path,
    source_path: Path,
    raw_text: str,
    digest: str,
) -> tuple[dict[str, Any], str, NormalizedEntry, list[str]]:
    """Infer front matter and normalized entry using the ingest agent."""
    config = load_config(root)
    fallback_sections = scan_headings(raw_text)
    warnings: list[str] = []

    if use_fake_llm():
        structured: IngestResult = _fake_structured_entry(source_path)
    else:
        agent = build_ingest_agent(config, model=config.model)
        structured = ingest_with_agent(agent, source_path=source_path, markdown=raw_text)

    normalized_dict, _ = normalization.normalized_from_structured(
        structured,
        source_path=relative_path(source_path, root),
        root=root,
        digest=digest,
        source_type=inputs.source_type,
        fallback_sections=fallback_sections,
        fallback_tags=[],
        fallback_summary=None,
    )
    normalized_entry = NormalizedEntry.model_validate(normalized_dict)

    frontmatter_data: dict[str, Any] = {
        "id": normalized_entry.id,
        "created_at": normalized_entry.created_at,
        "title": normalized_entry.title,
        "tags": list(normalized_entry.tags or []),
    }
    if normalized_entry.summary:
        frontmatter_data["summary"] = normalized_entry.summary

    warnings.append("front matter synthesized via ingest agent")
    return frontmatter_data, raw_text.strip(), normalized_entry, warnings


def _resolve_title(inputs: CaptureInput, body: str) -> str:
    if inputs.title:
        return inputs.title
    stripped = body.strip().splitlines()
    if stripped:
        return stripped[0][:120]
    return "Captured Entry"


def _build_manifest_entry(
    *,
    digest: str,
    markdown_path: Path,
    normalized_path: Path,
    source_type: str,
    created_at: str,
    slug: str,
    tags: list[str],
    root: Path,
    canonical_path: Path | None = None,
    snapshot_path: Path | None = None,
    aliases: Sequence[str] | None = None,
) -> ManifestEntry:
    canonical_rel = (
        relative_path(canonical_path, root)
        if canonical_path is not None
        else relative_path(markdown_path, root)
    )
    snapshot_rel = relative_path(snapshot_path, root) if snapshot_path is not None else None
    return ManifestEntry(
        hash=digest,
        path=relative_path(markdown_path, root),
        normalized=relative_path(normalized_path, root),
        source_type=source_type,
        ingested_at=time_utils.format_timestamp(time_utils.now()),
        created_at=created_at,
        id=slug,
        tags=tags,
        model=None,
        canonical_journal_path=canonical_rel,
        snapshot_path=snapshot_rel,
        aliases=list(aliases or []),
    )


def _coalesce_tags(*tag_sets: Iterable[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for tags in tag_sets:
        for tag in tags:
            if tag not in seen:
                ordered.append(tag)
                seen.add(tag)
    return ordered


class EntryResult(BaseModel):
    """Outcome for a single journal entry processed during capture."""

    markdown_path: str | None = Field(None, description="Authoritative Markdown path.")
    normalized_path: str | None = Field(None, description="Normalized YAML emitted for the entry.")
    date: str = Field(..., description="Date bucket for the entry (YYYY-MM-DD).")
    slug: str = Field(..., description="Slug assigned to the entry.")
    deduped: bool = Field(
        False,
        description="True when the input was skipped due to identical hash.",
    )
    changed: bool = Field(False, description="True when content or metadata changed on disk.")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal issues encountered.")
    source_hash: str | None = Field(
        None,
        description="Hash of the Markdown content used for dedupe/normalization.",
    )
    source_type: str | None = Field(
        None,
        description="Source type recorded for the entry (journal/notes/blog).",
    )


def _persist_file_entry(
    inputs: CaptureInput,
    root: Path,
    config: AppConfig,
    manifest_entries: list[ManifestEntry],
    *,
    source_path: Path | None = None,
    snapshot: bool = True,
    manifest_index_cache: dict[str, ManifestEntry] | None = None,
) -> EntryResult:
    if source_path is None:
        if not inputs.paths:
            msg = "capture --from requires at least one path"
            raise ValueError(msg)
        source_path = Path(inputs.paths[0]).expanduser().resolve()
    else:
        source_path = source_path.expanduser().resolve()

    ensure_manifest(manifest_entries, root, config)
    manifest_path = _manifest_path(root, config)
    local_index = (
        manifest_index_cache
        if manifest_index_cache is not None
        else _manifest_index(manifest_entries)
    )

    raw_bytes = source_path.read_bytes()
    digest = digest_bytes(raw_bytes)

    if digest in local_index:
        existing = local_index[digest]
        return EntryResult(
            markdown_path=existing.path,
            normalized_path=existing.normalized,
            date=existing.created_at[:10],
            slug=existing.id,
            deduped=True,
            changed=False,
            warnings=[],
            source_hash=digest,
            source_type=existing.source_type,
        )

    text = raw_bytes.decode("utf-8")
    normalized_seed: NormalizedEntry | None = None
    ingest_warnings: list[str] = []
    try:
        frontmatter_data, body = split_frontmatter(text)
        body = body.strip()
    except Exception:  # noqa: BLE001 - tolerate malformed front matter
        tolerant = split_frontmatter_tolerant(text)
        ingest_warnings.extend(tolerant.warnings)
        frontmatter_data = tolerant.data
        body = tolerant.body.strip()
        if not frontmatter_data:
            frontmatter_data, body, normalized_seed, ingest_warnings = _ingest_frontmatter(
                inputs,
                root=root,
                source_path=source_path,
                raw_text=text,
                digest=digest,
            )

    # Normalize common date field names (Jekyll/WordPress 'date') to 'created_at'
    if not frontmatter_data.get("created_at") and frontmatter_data.get("date"):
        frontmatter_data["created_at"] = frontmatter_data["date"]

    if not frontmatter_data.get("created_at") and inputs.date is None:
        inferred_dt, inferred_reason = infer_created_at_from_context(
            source_path=source_path,
            body=body,
        )
        if inferred_dt and inferred_reason:
            frontmatter_data["created_at"] = time_utils.format_timestamp(inferred_dt)
            ingest_warnings.append(f"created_at inferred from {inferred_reason}")

    created_dt = resolve_created_dt(
        frontmatter_data.get("created_at") or inputs.date,
        time_utils.now(),
    )
    date_str = created_dt.strftime("%Y-%m-%d")

    title_raw = frontmatter_data.get("title") or _resolve_title(inputs, body)
    title = str(title_raw)
    slug_source = frontmatter_data.get("id") or frontmatter_data.get("slug") or inputs.slug
    if slug_source is not None:
        slug_source = str(slug_source)
    else:
        slug_source = f"{date_str}-{time_utils.slugify_title(title)}"
    slug = ensure_unique_slug(root, date_str, slug_source)

    aliases: list[str] = []
    entry_warnings: list[str] = list(ingest_warnings)
    if slug != slug_source:
        aliases.append(slug_source)
        entry_warnings.append(f'slug "{slug_source}" already exists; stored as "{slug}"')

    tags = _coalesce_tags(
        coerce_frontmatter_tags(frontmatter_data.get("tags")),
        inputs.tags,
    )
    projects = _coalesce_tags(
        coerce_frontmatter_tags(frontmatter_data.get("projects")),
        inputs.projects,
    )

    markdown_path = journal_path(root, date_str, slug)
    canonical_rel = relative_path(markdown_path, root)
    frontmatter_out: dict[str, Any] = {
        "id": slug,
        "created_at": time_utils.format_timestamp(created_dt),
        "title": title,
        "tags": tags,
        "source_type": inputs.source_type,
        "origin": {
            "kind": "import",
            "original_path": str(source_path),
            "import_hash": digest,
            "canonical_path": canonical_rel,
        },
    }
    if projects:
        frontmatter_out["projects"] = projects
    mood = frontmatter_data.get("mood") or inputs.mood
    if mood:
        frontmatter_out["mood"] = mood
    summary_text = _derive_summary_text(frontmatter_data.get("summary"), body)
    if summary_text:
        frontmatter_out["summary"] = summary_text

    for key, value in frontmatter_data.items():
        if key not in frontmatter_out:
            frontmatter_out[key] = value

    snapshot_path_obj: Path | None = None
    if snapshot:
        snapshot_path_obj = write_snapshot(raw_bytes, root, digest)
        frontmatter_out["origin"]["snapshot_path"] = relative_path(snapshot_path_obj, root)

    write_markdown_entry(markdown_path, frontmatter_out, body)

    normalized_path = normalized_entry_path(root, date_str, slug, paths=config.paths)
    if normalized_seed is not None:
        normalized_seed.id = slug
        normalized_seed.created_at = time_utils.format_timestamp(created_dt)
        normalized_seed.source_path = relative_path(markdown_path, root)
        normalized_seed.source_hash = digest
        normalized_seed.source_type = inputs.source_type
        normalized_seed.tags = tags
        if summary_text:
            normalized_seed.summary = summary_text
        normalized_seed.content = body.strip() if body.strip() else None
        normalized_payload = normalized_seed.model_dump(mode="python")
        write_yaml_if_changed(normalized_path, normalized_payload)
    else:
        normalized_path, _normalized_changed = normalize_markdown(
            markdown_path,
            root=root,
            config=config,
            source_hash=digest,
            source_type=inputs.source_type,
        )

    entry = _build_manifest_entry(
        digest=digest,
        markdown_path=markdown_path,
        normalized_path=normalized_path,
        source_type=inputs.source_type,
        created_at=time_utils.format_timestamp(created_dt),
        slug=slug,
        tags=tags,
        root=root,
        canonical_path=markdown_path,
        snapshot_path=snapshot_path_obj,
        aliases=aliases,
    )
    manifest_entries.append(entry)
    write_manifest(manifest_path, manifest_entries)
    local_index[digest] = entry

    return EntryResult(
        markdown_path=relative_path(markdown_path, root),
        normalized_path=relative_path(normalized_path, root),
        date=date_str,
        slug=slug,
        deduped=False,
        changed=True,
        warnings=entry_warnings,
        source_hash=digest,
        source_type=inputs.source_type,
    )


def _persist_text_entry(
    inputs: CaptureInput,
    root: Path,
    config: AppConfig,
    manifest_entries: list[ManifestEntry],
) -> EntryResult:
    ensure_manifest(manifest_entries, root, config)
    manifest_path = _manifest_path(root, config)
    manifest_index = _manifest_index(manifest_entries)

    now_dt = time_utils.now()
    created_dt = resolve_created_dt(inputs.date, now_dt)
    date_str = created_dt.strftime("%Y-%m-%d")

    body_text = (inputs.text or "").strip()
    title = _resolve_title(inputs, body_text)
    base_slug = inputs.slug or f"{date_str}-{time_utils.slugify_title(title)}"
    slug = ensure_unique_slug(root, date_str, base_slug)
    aliases: list[str] = []
    entry_warnings: list[str] = []
    if slug != base_slug:
        aliases.append(base_slug)
        entry_warnings.append(f'slug "{base_slug}" already exists; stored as "{slug}"')

    markdown_path = journal_path(root, date_str, slug)
    frontmatter_tags = _coalesce_tags(inputs.tags)
    projects = _coalesce_tags(inputs.projects)

    frontmatter: dict[str, Any] = {
        "id": slug,
        "created_at": time_utils.format_timestamp(created_dt),
        "title": title,
        "tags": frontmatter_tags,
        "source_type": inputs.source_type,
        "origin": {"kind": "capture"},
    }
    frontmatter["origin"]["canonical_path"] = relative_path(markdown_path, root)
    if projects:
        frontmatter["projects"] = projects
    if inputs.mood:
        frontmatter["mood"] = inputs.mood
    summary_text = _derive_summary_text(None, body_text)
    if summary_text:
        frontmatter["summary"] = summary_text

    content = dump_yaml(frontmatter, sort_keys=False).strip()
    markdown_content = f"---\n{content}\n---\n"
    if body_text:
        markdown_content += f"\n{body_text}\n"
    else:
        markdown_content += "\n"
    digest = digest_text(markdown_content)
    if digest in manifest_index:
        # Entry already exists with identical content.
        existing = manifest_index[digest]
        return EntryResult(
            markdown_path=existing.path,
            normalized_path=existing.normalized,
            date=existing.created_at[:10],
            slug=existing.id,
            deduped=True,
            changed=False,
            warnings=[],
            source_hash=digest,
            source_type=existing.source_type,
        )

    write_markdown_entry(markdown_path, frontmatter, body_text)

    normalized_path, _normalized_changed = normalize_markdown(
        markdown_path,
        root=root,
        config=config,
        source_hash=digest,
        source_type=inputs.source_type,
    )

    entry = _build_manifest_entry(
        digest=digest,
        markdown_path=markdown_path,
        normalized_path=normalized_path,
        source_type=inputs.source_type,
        created_at=time_utils.format_timestamp(created_dt),
        slug=slug,
        tags=frontmatter_tags,
        root=root,
        canonical_path=markdown_path,
        aliases=aliases,
    )
    manifest_entries.append(entry)
    write_manifest(manifest_path, manifest_entries)
    manifest_index[digest] = entry

    return EntryResult(
        markdown_path=relative_path(markdown_path, root),
        normalized_path=relative_path(normalized_path, root),
        date=date_str,
        slug=slug,
        deduped=False,
        changed=True,
        warnings=entry_warnings,
        source_hash=digest,
        source_type=inputs.source_type,
    )


def run_persist_stage_0(
    inputs: CaptureInput,
    root: Path,
    config: AppConfig,
    manifest_entries: list[ManifestEntry],
    log_event: Callable[[dict[str, object]], None],
) -> PersistStage0Outputs:
    from aijournal.services.capture import PersistStage0Outputs
    from aijournal.services.capture.results import OperationResult
    from aijournal.services.capture.utils import discover_markdown_files, ensure_manifest
    from aijournal.services.capture.utils import manifest_index as _manifest_index

    entry_results: list[EntryResult] = []
    stage_entry_warnings: list[str] = []

    persist_start = perf_counter()
    if inputs.source in {"stdin", "editor"}:
        if not inputs.text:
            msg = "capture text input requires non-empty text"
            log_event({"event": "persist", "status": "error", "error": msg})
            raise ValueError(msg)
        entry = _persist_text_entry(inputs, root, config, manifest_entries)
        stage_entry_warnings.extend(entry.warnings)
        entry_results.append(entry)
    else:
        if not inputs.paths:
            msg = "capture --from requires at least one path"
            log_event({"event": "persist", "status": "error", "error": msg})
            raise ValueError(msg)
        files = discover_markdown_files(inputs.paths)
        if not files:
            msg = "capture --from found no Markdown files"
            log_event({"event": "persist", "status": "error", "error": msg})
            raise ValueError(msg)
        ensure_manifest(manifest_entries, root, config)
        manifest_idx = _manifest_index(manifest_entries)
        for file_path in files:
            entry = _persist_file_entry(
                inputs,
                root,
                config,
                manifest_entries,
                source_path=file_path,
                snapshot=inputs.snapshot,
                manifest_index_cache=manifest_idx,
            )
            stage_entry_warnings.extend(entry.warnings)
            entry_results.append(entry)

    duration_ms = (perf_counter() - persist_start) * 1000.0
    created_count = sum(1 for entry in entry_results if entry.changed and not entry.deduped)
    deduped_count = sum(1 for entry in entry_results if entry.deduped)
    artifacts = [
        entry.markdown_path
        for entry in entry_results
        if entry.changed and not entry.deduped and entry.markdown_path
    ]
    persist_details: dict[str, object] = {
        "entries": len(entry_results),
        "created": created_count,
        "deduped": deduped_count,
    }
    message = f"{created_count} entries persisted" if created_count else "no new entries persisted"
    op_result = OperationResult.wrote(
        artifacts,
        message=message,
        warnings=stage_entry_warnings,
        details=persist_details,
    )
    return PersistStage0Outputs(entry_results, op_result, duration_ms)
