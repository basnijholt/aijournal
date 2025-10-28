"""Schemas and entry point for the capture orchestrator (Phase 2 scaffold)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from aijournal.models import JournalSection, ManifestEntry, NormalizedEntry
from aijournal.utils import time as time_utils
from aijournal.utils.paths import normalized_entry_path


def _journal_path(root: Path, date_str: str, slug: str) -> Path:
    date = datetime.strptime(date_str, "%Y-%m-%d")
    return (
        root
        / "data"
        / "journal"
        / date.strftime("%Y")
        / date.strftime("%m")
        / date.strftime("%d")
        / f"{slug}.md"
    )


def _manifest_path(root: Path) -> Path:
    return root / "data" / "manifest" / "ingested.yaml"


def _load_manifest(path: Path) -> list[ManifestEntry]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not raw:
        return []
    return [ManifestEntry.model_validate(entry) for entry in raw]


def _write_manifest(path: Path, entries: Iterable[ManifestEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [entry.model_dump(mode="python") for entry in entries]
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _manifest_index(entries: Iterable[ManifestEntry]) -> dict[str, ManifestEntry]:
    return {entry.hash: entry for entry in entries}


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _write_markdown_entry(path: Path, frontmatter: dict[str, object], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml_block = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    content = f"---\n{yaml_block}\n---\n"
    if body:
        content += f"\n{body.strip()}\n"
    else:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def _write_yaml(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _load_existing_yaml(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    return data


def _write_yaml_if_changed(path: Path, payload: dict[str, object]) -> bool:
    existing = _load_existing_yaml(path)
    if existing == payload:
        return False
    _write_yaml(path, payload)
    return True


def _ensure_unique_slug(root: Path, date_str: str, base_slug: str) -> str:
    slug = base_slug
    counter = 2
    while _journal_path(root, date_str, slug).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def _digest_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def _digest_text(text: str) -> str:
    return _digest_bytes(text.encode("utf-8"))


def _ensure_manifest(entries: list[ManifestEntry], root: Path) -> None:
    if entries:
        return
    entries.extend(_load_manifest(_manifest_path(root)))


def _resolve_created_dt(preferred: object, fallback: datetime) -> datetime:
    if preferred:
        if isinstance(preferred, datetime):
            parsed = preferred
        elif (
            hasattr(preferred, "year") and hasattr(preferred, "month") and hasattr(preferred, "day")
        ):
            parsed = datetime(preferred.year, preferred.month, preferred.day, tzinfo=UTC)
        else:
            text = str(preferred)
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                parsed = datetime.strptime(text, "%Y-%m-%d")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    return fallback


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
) -> ManifestEntry:
    return ManifestEntry(
        hash=digest,
        path=_relative_path(markdown_path, root),
        normalized=_relative_path(normalized_path, root),
        source_type=source_type,
        ingested_at=time_utils.format_timestamp(time_utils.now()),
        created_at=created_at,
        id=slug,
        tags=tags,
        model=None,
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


def _coerce_frontmatter_tags(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw if isinstance(item, (str, int, float))]
    if isinstance(raw, str):
        return [raw]
    return []


def _normalize_markdown(
    markdown_path: Path,
    *,
    root: Path,
    source_hash: str,
    source_type: str,
) -> tuple[Path, bool]:
    frontmatter, body = _split_frontmatter(markdown_path.read_text(encoding="utf-8"))

    created_dt = _resolve_created_dt(frontmatter.get("created_at"), time_utils.now())
    created_str = time_utils.format_timestamp(created_dt)
    date_str = created_dt.strftime("%Y-%m-%d")

    entry_id_raw = frontmatter.get("id") or frontmatter.get("slug")
    if entry_id_raw is None:
        entry_id_raw = markdown_path.stem
    entry_id = str(entry_id_raw)

    title_raw = frontmatter.get("title") or entry_id.replace("-", " ").title()
    title = str(title_raw)

    tags = _coerce_frontmatter_tags(frontmatter.get("tags"))
    sections_raw = _scan_headings(body)
    sections_models: list[JournalSection] = []
    for section in sections_raw:
        heading = str(section.get("heading", title))
        level_raw = section.get("level", 1)
        if isinstance(level_raw, (int, float, str)):
            try:
                level = int(level_raw)
            except (TypeError, ValueError):
                level = 1
        else:
            level = 1
        sections_models.append(
            JournalSection(
                heading=heading,
                level=level,
                summary=None,
            ),
        )
    summary_raw = frontmatter.get("summary")
    summary_text = str(summary_raw) if summary_raw is not None else (body.strip() or None)
    if not sections_models:
        sections_models = [JournalSection(heading=title, level=1, summary=summary_text)]

    normalized_entry = NormalizedEntry(
        id=entry_id,
        created_at=created_str,
        source_path=_relative_path(markdown_path, root),
        title=title,
        tags=tags,
        sections=sections_models,
        summary=summary_text,
        source_hash=source_hash,
        source_type=source_type,
    )
    normalized_path = normalized_entry_path(root, date_str, entry_id)
    changed = _write_yaml_if_changed(
        normalized_path,
        normalized_entry.model_dump(mode="python"),
    )
    return normalized_path, changed


def _persist_text_entry(
    inputs: CaptureInput,
    root: Path,
    manifest_entries: list[ManifestEntry],
) -> EntryResult:
    _ensure_manifest(manifest_entries, root)
    manifest_path = _manifest_path(root)
    manifest_index = _manifest_index(manifest_entries)

    now_dt = time_utils.now()
    created_dt = _resolve_created_dt(inputs.date, now_dt)
    date_str = created_dt.strftime("%Y-%m-%d")

    body_text = (inputs.text or "").strip()
    title = _resolve_title(inputs, body_text)
    base_slug = inputs.slug or f"{date_str}-{time_utils.slugify_title(title)}"
    slug = _ensure_unique_slug(root, date_str, base_slug)

    markdown_path = _journal_path(root, date_str, slug)
    frontmatter_tags = _coalesce_tags(inputs.tags)
    projects = _coalesce_tags(inputs.projects)

    frontmatter: dict[str, object] = {
        "id": slug,
        "created_at": time_utils.format_timestamp(created_dt),
        "title": title,
        "tags": frontmatter_tags,
        "source_type": inputs.source_type,
        "origin": {"kind": "capture"},
    }
    if projects:
        frontmatter["projects"] = projects
    if inputs.mood:
        frontmatter["mood"] = inputs.mood
    summary_text = body_text or None
    if summary_text:
        frontmatter["summary"] = summary_text

    content = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    markdown_content = f"---\n{content}\n---\n"
    if body_text:
        markdown_content += f"\n{body_text}\n"
    else:
        markdown_content += "\n"
    digest = _digest_text(markdown_content)
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

    _write_markdown_entry(markdown_path, frontmatter, body_text)

    normalized_path, normalized_changed = _normalize_markdown(
        markdown_path,
        root=root,
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
    )
    manifest_entries.append(entry)
    _write_manifest(manifest_path, manifest_entries)
    manifest_index[digest] = entry

    return EntryResult(
        markdown_path=_relative_path(markdown_path, root),
        normalized_path=_relative_path(normalized_path, root),
        date=date_str,
        slug=slug,
        deduped=False,
        changed=True,
        warnings=[],
        source_hash=digest,
        source_type=inputs.source_type,
    )


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
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
    data = yaml.safe_load(frontmatter_raw) or {}
    if not isinstance(data, dict):
        data = {}
    return data, body


def _scan_headings(text: str) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        hashes, _, heading = stripped.partition(" ")
        if not heading:
            continue
        level = len(hashes)
        sections.append({"heading": heading.strip(), "level": level})
    return sections


def _persist_file_entry(
    inputs: CaptureInput,
    root: Path,
    manifest_entries: list[ManifestEntry],
) -> EntryResult:
    if not inputs.paths:
        raise ValueError("capture --from requires at least one path")

    _ensure_manifest(manifest_entries, root)
    manifest_path = _manifest_path(root)
    manifest_index = _manifest_index(manifest_entries)

    source_path = Path(inputs.paths[0]).expanduser().resolve()
    raw_bytes = source_path.read_bytes()
    digest = _digest_bytes(raw_bytes)

    if digest in manifest_index:
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

    text = raw_bytes.decode("utf-8")
    frontmatter_data, body = _split_frontmatter(text)
    body = body.strip()

    created_dt = _resolve_created_dt(
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
    slug = _ensure_unique_slug(root, date_str, slug_source)

    tags = _coalesce_tags(
        _coerce_frontmatter_tags(frontmatter_data.get("tags")),
        inputs.tags,
    )
    projects = _coalesce_tags(
        _coerce_frontmatter_tags(frontmatter_data.get("projects")),
        inputs.projects,
    )

    markdown_path = _journal_path(root, date_str, slug)
    frontmatter_out: dict[str, object] = {
        "id": slug,
        "created_at": time_utils.format_timestamp(created_dt),
        "title": title,
        "tags": tags,
        "source_type": inputs.source_type,
        "origin": {
            "kind": "import",
            "original_path": str(source_path),
            "import_hash": digest,
        },
    }
    if projects:
        frontmatter_out["projects"] = projects
    mood = frontmatter_data.get("mood") or inputs.mood
    if mood:
        frontmatter_out["mood"] = mood
    summary_raw = frontmatter_data.get("summary")
    if summary_raw is not None:
        summary_text = str(summary_raw)
    elif body:
        summary_text = body
    else:
        summary_text = None
    if summary_text:
        frontmatter_out["summary"] = summary_text

    # Preserve any unhandled keys from the original frontmatter.
    for key, value in frontmatter_data.items():
        if key not in frontmatter_out:
            frontmatter_out[key] = value

    _write_markdown_entry(markdown_path, frontmatter_out, body)

    normalized_path, normalized_changed = _normalize_markdown(
        markdown_path,
        root=root,
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
    )
    manifest_entries.append(entry)
    _write_manifest(manifest_path, manifest_entries)
    manifest_index[digest] = entry

    return EntryResult(
        markdown_path=_relative_path(markdown_path, root),
        normalized_path=_relative_path(normalized_path, root),
        date=date_str,
        slug=slug,
        deduped=False,
        changed=True,
        warnings=[],
        source_hash=digest,
        source_type=inputs.source_type,
    )


class CaptureInput(BaseModel):
    """User-provided options for a capture run."""

    source: Literal["stdin", "editor", "file", "dir"] = Field(
        ...,
        description="Primary source for captured content.",
    )
    text: str | None = Field(None, description="Raw text provided on the CLI.")
    paths: list[str] = Field(default_factory=list, description="Paths to capture from.")
    source_type: Literal["journal", "notes", "blog"] = Field(
        "journal",
        description="Semantic classification of the captured material.",
    )
    date: str | None = Field(None, description="Override created_at date (YYYY-MM-DD).")
    title: str | None = Field(None, description="Override title for captured entries.")
    slug: str | None = Field(None, description="Explicit slug to use when persisting.")
    tags: list[str] = Field(default_factory=list, description="Tags to merge into front matter.")
    projects: list[str] = Field(
        default_factory=list,
        description="Projects to merge into front matter.",
    )
    mood: str | None = Field(None, description="Mood value to record in front matter.")
    apply_profile: Literal["auto", "review"] = Field(
        "auto",
        description="How profile updates should be applied after derivations.",
    )
    rebuild: Literal["auto", "always", "skip"] = Field(
        "auto",
        description="How index/persona rebuilds should be triggered.",
    )
    pack: Literal["L1", "L3", "L4"] | None = Field(
        None,
        description="Optional pack level to emit when persona changes.",
    )
    retries: int = Field(1, ge=0, description="LLM structured-output retries per stage.")
    progress: bool = Field(True, description="Whether to display progress indicators.")
    dry_run: bool = Field(False, description="Skip writes and report planned actions only.")


class EntryResult(BaseModel):
    """Outcome for a single journal entry processed during capture."""

    markdown_path: str | None = Field(None, description="Authoritative Markdown path.")
    normalized_path: str | None = Field(None, description="Normalized YAML emitted for the entry.")
    date: str = Field(..., description="Date bucket for the entry (YYYY-MM-DD).")
    slug: str = Field(..., description="Slug assigned to the entry.")
    deduped: bool = Field(
        False, description="True when the input was skipped due to identical hash."
    )
    changed: bool = Field(False, description="True when content or metadata changed on disk.")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal issues encountered.")
    source_hash: str | None = Field(
        None, description="Hash of the Markdown content used for dedupe/normalization."
    )
    source_type: str | None = Field(
        None, description="Source type recorded for the entry (journal/notes/blog)."
    )


class CaptureResult(BaseModel):
    """Aggregate result for a capture run."""

    run_id: str = Field(..., description="Unique identifier for the capture run.")
    entries: list[EntryResult] = Field(default_factory=list, description="Per-entry outcomes.")
    artifacts_changed: dict[str, int] = Field(
        default_factory=dict,
        description="Counts of downstream artifacts touched by type.",
    )
    persona_stale_before: bool = Field(
        False,
        description="Whether persona was stale before capture executed.",
    )
    persona_stale_after: bool = Field(
        False,
        description="Whether persona remains stale after capture steps.",
    )
    index_rebuilt: bool = Field(False, description="True when the index was fully rebuilt.")
    warnings: list[str] = Field(default_factory=list, description="Warnings raised during capture.")
    errors: list[str] = Field(default_factory=list, description="Fatal errors encountered.")
    durations_ms: dict[str, float] = Field(
        default_factory=dict,
        description="Per-stage durations (milliseconds).",
    )


def run_capture(_input: CaptureInput) -> CaptureResult:
    """Execute the capture workflow (stub for Phase 2)."""

    raise NotImplementedError("capture service not implemented yet")


def normalize_entries(entries: list[EntryResult], root: Path) -> dict[str, int]:
    """Normalize Markdown entries that changed during capture."""

    normalized = 0
    for entry in entries:
        if not entry.markdown_path:
            continue
        if not entry.changed and entry.normalized_path:
            # Assume already normalized when unchanged.
            continue
        markdown_path = root / entry.markdown_path
        if not markdown_path.exists():
            continue
        source_hash = entry.source_hash or _digest_bytes(markdown_path.read_bytes())
        source_type = entry.source_type or "journal"
        normalized_path, changed = _normalize_markdown(
            markdown_path,
            root=root,
            source_hash=source_hash,
            source_type=source_type,
        )
        if changed:
            normalized += 1
        entry.normalized_path = _relative_path(normalized_path, root)
    return {"normalized": normalized}
