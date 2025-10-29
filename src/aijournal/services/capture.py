"""Schemas and entry point for the capture orchestrator (Phase 2 scaffold)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import typer
import yaml
from pydantic import BaseModel, Field

from aijournal.commands.characterize import run_characterize
from aijournal.commands.facts import run_facts
from aijournal.commands.index import run_index_rebuild, run_index_tail
from aijournal.commands.ingest import _load_config
from aijournal.commands.pack import run_pack
from aijournal.commands.persona import persona_state, run_persona_build
from aijournal.commands.profile import (
    _apply_claim_upsert,
    _apply_profile_update,
    _load_profile_components,
    _profile_to_dict,
    run_profile_apply,
    run_profile_suggest,
)
from aijournal.commands.summarize import run_summarize as run_summarize_command
from aijournal.io.yaml_io import load_yaml_model, write_yaml_model
from aijournal.models import (
    ClaimAtom,
    ClaimProposal,
    ClaimsFile,
    FacetProposal,
    JournalSection,
    ManifestEntry,
    NormalizedEntry,
    ProfileUpdateBatch,
    SelfProfile,
)
from aijournal.utils import time as time_utils
from aijournal.utils.paths import normalized_entry_path

MARKDOWN_SUFFIXES = {".md", ".markdown"}


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


def _generate_run_id() -> str:
    """Return a monotonic-ish identifier for a capture run."""

    return f"capture-{time_utils.now().strftime('%Y%m%d%H%M%S')}"


def _pending_batches(root: Path) -> set[Path]:
    directory = root / "derived" / "pending" / "profile_updates"
    if not directory.exists():
        return set()
    return {path for path in directory.glob("*.yaml") if path.is_file()}


def _noop_preview(
    proposals: Sequence[ClaimProposal],
    claims: Sequence[ClaimAtom],
    timestamp: str,
) -> None:
    del proposals, claims, timestamp
    return None


def _apply_profile_update_batch(root: Path, batch_path: Path) -> bool:
    batch = load_yaml_model(batch_path, ProfileUpdateBatch)
    claim_proposals: list[ClaimProposal] = [
        proposal.model_copy(deep=True) for proposal in batch.proposals.claims
    ]
    facet_proposals: list[FacetProposal] = [
        proposal.model_copy(deep=True) for proposal in batch.proposals.facets
    ]

    profile_model, claim_models = _load_profile_components(root)
    profile = _profile_to_dict(profile_model)
    claims_data = [claim.model_copy(deep=True) for claim in claim_models]
    timestamp = time_utils.format_timestamp(time_utils.now())

    applied = False
    for claim_proposal in claim_proposals:
        if _apply_claim_upsert(claims_data, claim_proposal.claim, timestamp):
            applied = True

    for facet_proposal in facet_proposals:
        if not facet_proposal.path:
            continue
        if _apply_profile_update(profile, facet_proposal.path, facet_proposal.value, timestamp):
            applied = True

    if not applied:
        return False

    updated_profile = SelfProfile.model_validate(profile)
    updated_claims = [claim.model_copy(deep=True) for claim in claims_data]
    write_yaml_model(root / "profile" / "self_profile.yaml", updated_profile)
    write_yaml_model(root / "profile" / "claims.yaml", ClaimsFile(claims=updated_claims))
    return True


def _raw_snapshot_path(root: Path, digest: str) -> Path:
    return root / "data" / "raw" / f"{digest}.md"


def _write_snapshot(raw_bytes: bytes, root: Path, digest: str) -> Path:
    snapshot_path = _raw_snapshot_path(root, digest)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    if not snapshot_path.exists():
        snapshot_path.write_bytes(raw_bytes)
    return snapshot_path


def _discover_markdown_files(paths: Sequence[str]) -> list[Path]:
    collected: list[Path] = []
    for raw in paths:
        candidate = Path(raw).expanduser().resolve()
        if candidate.is_dir():
            for path in sorted(candidate.rglob("*")):
                if path.is_file() and path.suffix.lower() in MARKDOWN_SUFFIXES:
                    collected.append(path)
            continue
        if candidate.is_file():
            if candidate.suffix.lower() in MARKDOWN_SUFFIXES:
                collected.append(candidate)
            continue
        raise FileNotFoundError(f"capture --from path not found: {raw}")

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in sorted(collected):
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _telemetry_log_path(root: Path, run_id: str) -> Path:
    return root / "derived" / "logs" / "capture" / f"{run_id}.jsonl"


def _make_telemetry_logger(
    root: Path,
    run_id: str,
    *,
    sink: Callable[[dict[str, object]], None] | None = None,
) -> tuple[Callable[[dict[str, object]], None], Path]:
    log_path = _telemetry_log_path(root, run_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(event: dict[str, object]) -> None:
        payload = {
            "run_id": run_id,
            "timestamp": time_utils.format_timestamp(time_utils.now()),
            **event,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        if sink is not None:
            try:
                sink(payload)
            except Exception:  # pragma: no cover - defensive sink guard
                return

    return _write, log_path


def _capture_result_path(root: Path, run_id: str) -> Path:
    return root / "derived" / "logs" / "capture" / f"{run_id}.result.json"


def _write_capture_result(root: Path, result: CaptureResult) -> Path:
    path = _capture_result_path(root, result.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Persist JSON so other processes (FastAPI) can retrieve run metadata.
    payload = result.model_dump(mode="json")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_capture_result(root: Path, run_id: str) -> CaptureResult:
    path = _capture_result_path(root, run_id)
    if not path.exists():
        msg = f"capture run not found: {run_id}"
        raise FileNotFoundError(msg)
    data = json.loads(path.read_text(encoding="utf-8"))
    return CaptureResult.model_validate(data)


DEFAULT_TIMEOUT_SECONDS = 120.0


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
    canonical_path: Path | None = None,
    snapshot_path: Path | None = None,
    aliases: Sequence[str] | None = None,
) -> ManifestEntry:
    canonical_rel = (
        _relative_path(canonical_path, root)
        if canonical_path is not None
        else _relative_path(markdown_path, root)
    )
    snapshot_rel = _relative_path(snapshot_path, root) if snapshot_path is not None else None
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


def _coerce_frontmatter_tags(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw if isinstance(item, (str, int, float))]
    if isinstance(raw, str):
        return [raw]
    return []


def _extract_json_frontmatter_block(text: str) -> tuple[str, str]:
    depth = 0
    in_string = False
    escape = False
    start_index = None
    for index, char in enumerate(text):
        if start_index is None:
            if char.isspace():
                continue
            if char != "{":
                raise ValueError("JSON frontmatter must start with '{'")
            start_index = index
            depth = 1
            continue

        if in_string:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0 and start_index is not None:
                end_index = index + 1
                block = text[start_index:end_index]
                remainder = text[end_index:]
                return block, remainder
    raise ValueError("Unterminated JSON frontmatter block")


def _extract_json_frontmatter(text: str) -> tuple[dict[str, object], str]:
    block, body = _extract_json_frontmatter_block(text)
    try:
        data = json.loads(block) or {}
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise ValueError("Invalid JSON frontmatter") from exc
    if not isinstance(data, dict):
        data = {}
    return data, body.lstrip("\n")


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
    aliases: list[str] = []
    entry_warnings: list[str] = []
    if slug != base_slug:
        aliases.append(base_slug)
        entry_warnings.append(f'slug "{base_slug}" already exists; stored as "{slug}"')

    markdown_path = _journal_path(root, date_str, slug)
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
    frontmatter["origin"]["canonical_path"] = _relative_path(markdown_path, root)
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
        canonical_path=markdown_path,
        aliases=aliases,
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
        warnings=entry_warnings,
        source_hash=digest,
        source_type=inputs.source_type,
    )


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return _extract_json_frontmatter(stripped)

    delimiter = None
    if stripped.startswith("---"):
        delimiter = "---"
    elif stripped.startswith("+++"):
        delimiter = "+++"
    if delimiter is None:
        msg = "Markdown entry missing YAML/TOML frontmatter delimiter"
        raise ValueError(msg)

    parts = stripped.split(delimiter, 2)
    if len(parts) < 3:
        msg = "Incomplete YAML/TOML frontmatter block"
        raise ValueError(msg)

    frontmatter_raw = parts[1].strip()
    body = parts[2].lstrip("\n")
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
    *,
    source_path: Path | None = None,
    snapshot: bool = True,
    manifest_index: dict[str, ManifestEntry] | None = None,
) -> EntryResult:
    if source_path is None:
        if not inputs.paths:
            raise ValueError("capture --from requires at least one path")
        source_path = Path(inputs.paths[0]).expanduser().resolve()
    else:
        source_path = source_path.expanduser().resolve()

    _ensure_manifest(manifest_entries, root)
    manifest_path = _manifest_path(root)
    local_index = (
        manifest_index if manifest_index is not None else _manifest_index(manifest_entries)
    )

    raw_bytes = source_path.read_bytes()
    digest = _digest_bytes(raw_bytes)

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

    aliases: list[str] = []
    entry_warnings: list[str] = []
    if slug != slug_source:
        aliases.append(slug_source)
        entry_warnings.append(f'slug "{slug_source}" already exists; stored as "{slug}"')

    tags = _coalesce_tags(
        _coerce_frontmatter_tags(frontmatter_data.get("tags")),
        inputs.tags,
    )
    projects = _coalesce_tags(
        _coerce_frontmatter_tags(frontmatter_data.get("projects")),
        inputs.projects,
    )

    markdown_path = _journal_path(root, date_str, slug)
    canonical_rel = _relative_path(markdown_path, root)
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
    summary_raw = frontmatter_data.get("summary")
    if summary_raw is not None:
        summary_text = str(summary_raw)
    elif body:
        summary_text = body
    else:
        summary_text = None
    if summary_text:
        frontmatter_out["summary"] = summary_text

    for key, value in frontmatter_data.items():
        if key not in frontmatter_out:
            frontmatter_out[key] = value

    snapshot_path_obj: Path | None = None
    if snapshot:
        snapshot_path_obj = _write_snapshot(raw_bytes, root, digest)
        frontmatter_out["origin"]["snapshot_path"] = _relative_path(snapshot_path_obj, root)

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
        canonical_path=markdown_path,
        snapshot_path=snapshot_path_obj,
        aliases=aliases,
    )
    manifest_entries.append(entry)
    _write_manifest(manifest_path, manifest_entries)
    local_index[digest] = entry

    return EntryResult(
        markdown_path=_relative_path(markdown_path, root),
        normalized_path=_relative_path(normalized_path, root),
        date=date_str,
        slug=slug,
        deduped=False,
        changed=True,
        warnings=entry_warnings,
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
    snapshot: bool = Field(True, description="Store raw snapshots for file imports.")


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
    review_candidates: list[str] = Field(
        default_factory=list,
        description="Pending review batch paths generated during capture.",
    )
    telemetry_path: str | None = Field(
        None,
        description="Relative path to the NDJSON telemetry log for this run.",
    )


def run_capture(
    inputs: CaptureInput,
    *,
    run_id: str | None = None,
    event_sink: Callable[[dict[str, object]], None] | None = None,
    root: Path | None = None,
) -> CaptureResult:
    """Execute the capture workflow (persist, normalize, derive, telemetry)."""

    if inputs.dry_run:
        msg = "capture dry-run is not implemented yet"
        raise ValueError(msg)

    root = root or Path.cwd()
    resolved_run_id = run_id or _generate_run_id()
    log_event, telemetry_path = _make_telemetry_logger(root, resolved_run_id, sink=event_sink)
    log_event(
        {
            "event": "preflight",
            "source": inputs.source,
            "paths": inputs.paths,
            "snapshot": inputs.snapshot,
            "apply_profile": inputs.apply_profile,
            "rebuild": inputs.rebuild,
            "pack": inputs.pack,
        }
    )

    if inputs.source not in {"stdin", "editor", "file", "dir"}:
        msg = f"Unsupported capture source: {inputs.source}"
        log_event({"event": "preflight", "status": "error", "error": msg})
        raise ValueError(msg)

    manifest_entries: list[ManifestEntry] = []
    entry_results: list[EntryResult] = []
    durations_ms: dict[str, float] = {}
    warnings: list[str] = []
    review_candidates: list[str] = []

    persist_start = perf_counter()
    if inputs.source in {"stdin", "editor"}:
        if not inputs.text:
            msg = "capture text input requires non-empty text"
            log_event({"event": "persist", "status": "error", "error": msg})
            raise ValueError(msg)
        entry_results.append(_persist_text_entry(inputs, root, manifest_entries))
    else:
        if not inputs.paths:
            msg = "capture --from requires at least one path"
            log_event({"event": "persist", "status": "error", "error": msg})
            raise ValueError(msg)
        files = _discover_markdown_files(inputs.paths)
        if not files:
            msg = "capture --from found no Markdown files"
            log_event({"event": "persist", "status": "error", "error": msg})
            raise ValueError(msg)
        _ensure_manifest(manifest_entries, root)
        manifest_idx = _manifest_index(manifest_entries)
        for file_path in files:
            entry_result = _persist_file_entry(
                inputs,
                root,
                manifest_entries,
                source_path=file_path,
                snapshot=inputs.snapshot,
                manifest_index=manifest_idx,
            )
            entry_results.append(entry_result)
    durations_ms["persist"] = (perf_counter() - persist_start) * 1000.0
    created_count = sum(1 for entry in entry_results if entry.changed and not entry.deduped)
    deduped_count = sum(1 for entry in entry_results if entry.deduped)
    log_event(
        {
            "event": "persist",
            "status": "ok",
            "duration_ms": round(durations_ms["persist"], 3),
            "entries": len(entry_results),
            "created": created_count,
            "deduped": deduped_count,
        }
    )

    normalize_start = perf_counter()
    artifact_counts = normalize_entries(entry_results, root) if entry_results else {}
    durations_ms["normalize"] = (perf_counter() - normalize_start) * 1000.0
    log_event(
        {
            "event": "normalize",
            "status": "ok",
            "duration_ms": round(durations_ms["normalize"], 3),
            "artifacts": artifact_counts,
        }
    )

    artifacts_changed = {key: value for key, value in artifact_counts.items() if value}
    entries_changed = sum(1 for entry in entry_results if entry.changed and not entry.deduped)
    if entries_changed:
        artifacts_changed.setdefault("entries", entries_changed)

    changed_dates = sorted(
        {entry.date for entry in entry_results if entry.changed and not entry.deduped}
    )

    def _record_duration(stage: str, start: float) -> None:
        elapsed = (perf_counter() - start) * 1000.0
        durations_ms[stage] = durations_ms.get(stage, 0.0) + elapsed

    def _warn(stage: str, exc: BaseException) -> None:
        warnings.append(f"{stage}: {exc}")

    for date in changed_dates:
        stage = "derive.summarize"
        start = perf_counter()
        summary_event = {"event": stage, "date": date}
        try:
            summary_path = run_summarize_command(
                date,
                timeout=DEFAULT_TIMEOUT_SECONDS,
                retries=inputs.retries,
                progress=inputs.progress,
            )
        except typer.Exit as exc:
            if exc.exit_code not in (0,):
                _warn(stage, exc)
                log_event({**summary_event, "status": "error", "error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive
            _warn(stage, exc)
            log_event({**summary_event, "status": "error", "error": str(exc)})
        else:
            artifacts_changed["summaries"] = artifacts_changed.get("summaries", 0) + 1
            log_event(
                {
                    **summary_event,
                    "status": "ok",
                    "path": _relative_path(summary_path, root),
                }
            )
        finally:
            _record_duration(stage, start)

        stage = "derive.extract_facts"
        start = perf_counter()
        facts_event = {"event": stage, "date": date}
        try:
            _, facts_path = run_facts(
                date,
                timeout=DEFAULT_TIMEOUT_SECONDS,
                retries=inputs.retries,
                progress=inputs.progress,
                claim_models=_load_profile_components(root)[1],
                build_claim_preview=_noop_preview,
            )
        except typer.Exit as exc:
            if exc.exit_code not in (0,):
                _warn(stage, exc)
                log_event({**facts_event, "status": "error", "error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive
            _warn(stage, exc)
            log_event({**facts_event, "status": "error", "error": str(exc)})
        else:
            if facts_path:
                artifacts_changed["microfacts"] = artifacts_changed.get("microfacts", 0) + 1
                log_event(
                    {
                        **facts_event,
                        "status": "ok",
                        "path": _relative_path(facts_path, root),
                    }
                )
        finally:
            _record_duration(stage, start)

        stage = "derive.profile_suggest"
        start = perf_counter()
        suggestions_path: Path | None = None
        suggest_event = {"event": stage, "date": date}
        try:
            suggestions_path = run_profile_suggest(
                date,
                timeout=DEFAULT_TIMEOUT_SECONDS,
                retries=inputs.retries,
                progress=inputs.progress,
            )
        except typer.Exit as exc:
            if exc.exit_code not in (0,):
                _warn(stage, exc)
                log_event({**suggest_event, "status": "error", "error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive
            _warn(stage, exc)
            log_event({**suggest_event, "status": "error", "error": str(exc)})
        else:
            artifacts_changed["profile_suggestions"] = (
                artifacts_changed.get("profile_suggestions", 0) + 1
            )
            if suggestions_path:
                log_event(
                    {
                        **suggest_event,
                        "status": "ok",
                        "path": _relative_path(suggestions_path, root),
                    }
                )
        finally:
            _record_duration(stage, start)

        if inputs.apply_profile == "auto":
            stage = "derive.profile_apply"
            start = perf_counter()
            apply_event = {"event": stage, "date": date}
            try:
                run_profile_apply(
                    date,
                    suggestions_path=suggestions_path,
                    auto_confirm=True,
                )
            except typer.Exit as exc:
                if exc.exit_code not in (0,):
                    _warn(stage, exc)
                    log_event({**apply_event, "status": "error", "error": str(exc)})
            except Exception as exc:  # pragma: no cover - defensive
                _warn(stage, exc)
                log_event({**apply_event, "status": "error", "error": str(exc)})
            else:
                artifacts_changed["profile"] = artifacts_changed.get("profile", 0) + 1
                log_event({**apply_event, "status": "ok"})
            finally:
                _record_duration(stage, start)

        pending_before = _pending_batches(root)
        stage = "derive.characterize"
        start = perf_counter()
        created_batches: list[Path] = []
        characterize_event = {"event": stage, "date": date}
        try:
            batch_path = run_characterize(
                date,
                timeout=DEFAULT_TIMEOUT_SECONDS,
                retries=inputs.retries,
                progress=inputs.progress,
                build_claim_preview=_noop_preview,
            )
            created_batches.append(batch_path)
        except typer.Exit as exc:
            if exc.exit_code not in (0,):
                _warn(stage, exc)
                log_event({**characterize_event, "status": "error", "error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive
            _warn(stage, exc)
            log_event({**characterize_event, "status": "error", "error": str(exc)})
        else:
            artifacts_changed["characterize"] = artifacts_changed.get("characterize", 0) + 1
            for batch in created_batches:
                log_event(
                    {
                        **characterize_event,
                        "status": "ok",
                        "path": _relative_path(batch, root),
                    }
                )
        finally:
            _record_duration(stage, start)

        pending_after = _pending_batches(root)
        new_batches = sorted(pending_after - pending_before)
        for batch_path in created_batches:
            if batch_path not in new_batches:
                new_batches.append(batch_path)

        review_candidates.extend(_relative_path(path, root) for path in new_batches)

        if inputs.apply_profile == "auto" and new_batches:
            stage = "derive.review"
            for batch_path in new_batches:
                start = perf_counter()
                review_event = {
                    "event": stage,
                    "path": _relative_path(batch_path, root),
                }
                try:
                    if _apply_profile_update_batch(root, batch_path):
                        artifacts_changed["profile"] = artifacts_changed.get("profile", 0) + 1
                        log_event({**review_event, "status": "applied"})
                    else:
                        log_event({**review_event, "status": "noop"})
                except Exception as exc:  # pragma: no cover - defensive
                    _warn(stage, exc)
                    log_event({**review_event, "status": "error", "error": str(exc)})
                finally:
                    _record_duration(stage, start)
        elif new_batches:
            for batch_path in new_batches:
                log_event(
                    {
                        "event": "derive.review",
                        "path": _relative_path(batch_path, root),
                        "status": "pending",
                    }
                )

    if inputs.apply_profile != "auto" and "profile" not in artifacts_changed:
        artifacts_changed.setdefault("profile", 0)

    index_rebuilt = False
    persona_stale_before = False
    persona_stale_after = False
    persona_changed = False

    if changed_dates:
        stage = "refresh.index"
        start = perf_counter()
        index_message = ""
        index_updated = False
        index_error = False
        try:
            index_db = root / "derived" / "index" / "index.db"
            if not index_db.exists():
                index_message = run_index_rebuild(since=None, limit=None)
                index_rebuilt = True
                index_updated = True
            else:
                since = min(changed_dates)
                index_message = run_index_tail(since=since, days=7, limit=None)
                if not index_message or "already up to date" not in index_message.lower():
                    index_updated = True
        except typer.Exit as exc:
            if exc.exit_code not in (0,):
                _warn(stage, exc)
                log_event({"event": "index.update", "status": "error", "error": str(exc)})
                index_error = True
        except Exception as exc:  # pragma: no cover - defensive
            _warn(stage, exc)
            log_event({"event": "index.update", "status": "error", "error": str(exc)})
            index_error = True
        else:
            if index_updated:
                artifacts_changed["index"] = artifacts_changed.get("index", 0) + 1
        finally:
            _record_duration(stage, start)
        log_event(
            {
                "event": "index.rebuild" if index_rebuilt else "index.update",
                "status": "error" if index_error else ("ok" if index_updated else "noop"),
                "message": index_message,
            }
        )

        stage = "refresh.persona"
        start = perf_counter()
        persona_event = {"event": "persona.status"}
        should_build = False
        persona_error = False
        try:
            status_before, _ = persona_state(root)
            persona_stale_before = status_before != "fresh"
            should_build = status_before != "fresh" or artifacts_changed.get("profile", 0) > 0
            profile_model, claim_models = _load_profile_components(root)
            profile_payload = _profile_to_dict(profile_model)
            if should_build and (profile_payload or claim_models):
                config = _load_config(root)
                _, persona_changed = run_persona_build(
                    profile_payload,
                    claim_models,
                    config=config,
                    root=root,
                )
                if persona_changed:
                    artifacts_changed["persona"] = artifacts_changed.get("persona", 0) + 1
            status_after, _ = persona_state(root)
            persona_stale_after = status_after != "fresh"
        except typer.Exit as exc:
            if exc.exit_code not in (0,):
                _warn(stage, exc)
                log_event({**persona_event, "status": "error", "error": str(exc)})
                persona_error = True
        except Exception as exc:  # pragma: no cover - defensive
            _warn(stage, exc)
            log_event({**persona_event, "status": "error", "error": str(exc)})
            persona_error = True
        finally:
            _record_duration(stage, start)
        log_event(
            {
                **persona_event,
                "status": "error" if persona_error else "ok",
                "before": "stale" if persona_stale_before else "fresh",
                "after": "stale" if persona_stale_after else "fresh",
            }
        )
        if should_build and not persona_error:
            log_event(
                {
                    "event": "persona.build",
                    "status": "ok" if persona_changed else "noop",
                }
            )

    if inputs.pack and persona_changed:
        stage = "refresh.pack"
        start = perf_counter()
        level = inputs.pack.upper()
        history_days = 1 if level == "L4" else 0
        pack_output = root / "derived" / "packs" / f"{level.lower()}_{resolved_run_id}.yaml"
        pack_error = False
        try:
            run_pack(
                level,
                None,
                output=pack_output,
                max_tokens=None,
                fmt="yaml",
                history_days=history_days,
                dry_run=False,
            )
            artifacts_changed["pack"] = artifacts_changed.get("pack", 0) + 1
        except typer.Exit as exc:
            if exc.exit_code not in (0,):
                _warn(stage, exc)
                log_event({"event": "pack", "status": "error", "error": str(exc)})
                pack_error = True
        except Exception as exc:  # pragma: no cover - defensive
            _warn(stage, exc)
            log_event({"event": "pack", "status": "error", "error": str(exc)})
            pack_error = True
        finally:
            _record_duration(stage, start)
        log_event(
            {
                "event": "pack",
                "status": "error" if pack_error else "ok",
                "level": level,
                "output": _relative_path(pack_output, root),
            }
        )

    for entry in entry_results:
        if entry.warnings:
            warnings.extend(entry.warnings)

    telemetry_rel = _relative_path(telemetry_path, root)
    log_event(
        {
            "event": "done",
            "status": "ok",
            "warnings": warnings,
            "artifacts_changed": artifacts_changed,
            "review_candidates": review_candidates,
        }
    )

    result = CaptureResult(
        run_id=resolved_run_id,
        entries=entry_results,
        artifacts_changed=artifacts_changed,
        persona_stale_before=persona_stale_before,
        persona_stale_after=persona_stale_after,
        index_rebuilt=index_rebuilt,
        durations_ms={key: round(value, 3) for key, value in durations_ms.items()},
        warnings=warnings,
        review_candidates=review_candidates,
        telemetry_path=telemetry_rel,
    )

    _write_capture_result(root, result)

    return result


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
