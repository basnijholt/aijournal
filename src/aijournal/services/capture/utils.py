"""Shared helper utilities for the capture orchestrator and stages."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from aijournal.commands.profile import (
    apply_claim_upsert,
    apply_profile_update,
    load_profile_components,
    profile_to_dict,
)
from aijournal.common.constants import MARKDOWN_SUFFIXES
from aijournal.domain.claims import ClaimAtom, ClaimSource
from aijournal.domain.evidence import redact_source_text
from aijournal.domain.journal import NormalizedEntry
from aijournal.io.artifacts import load_artifact_data
from aijournal.io.yaml_io import dump_yaml, write_yaml_model
from aijournal.models.authoritative import ClaimsFile, JournalSection, ManifestEntry, SelfProfile
from aijournal.models.derived import ProfileUpdateBatch
from aijournal.pipelines import normalization
from aijournal.services.capture.tolerant import (
    parse_date_tolerant,
    split_frontmatter_tolerant,
)
from aijournal.utils import time as time_utils
from aijournal.utils.paths import normalized_entry_path
from aijournal.utils.text import strip_invisible_prefix

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from aijournal.common.app_config import AppConfig
    from aijournal.domain.changes import ClaimProposal, FacetChange
    from aijournal.services.capture.results import OperationResult

logger = logging.getLogger(__name__)


def journal_path(root: Path, date_str: str, slug: str) -> Path:
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        parsed = parse_date_tolerant(date_str, fallback=datetime.now(tz=UTC))
        for warning in parsed.warnings:
            logger.warning("tolerant date parsing while building journal path: %s", warning)
        date = parsed.dt
    return (
        root
        / "data"
        / "journal"
        / date.strftime("%Y")
        / date.strftime("%m")
        / date.strftime("%d")
        / f"{slug}.md"
    )


def manifest_path(workspace: Path, config: AppConfig) -> Path:
    """Get manifest file path for a workspace."""
    from aijournal.utils.paths import resolve_path

    return resolve_path(workspace, config, "data/manifest/ingested.yaml")


def load_manifest(path: Path) -> list[ManifestEntry]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not raw:
        return []
    return [ManifestEntry.model_validate(entry) for entry in raw]


def write_manifest(path: Path, entries: Iterable[ManifestEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [entry.model_dump(mode="python") for entry in entries]
    path.write_text(dump_yaml(payload, sort_keys=False), encoding="utf-8")


def manifest_index(entries: Iterable[ManifestEntry]) -> dict[str, ManifestEntry]:
    return {entry.hash: entry for entry in entries}


def ensure_manifest(entries: list[ManifestEntry], root: Path, config: AppConfig) -> None:
    if entries:
        return
    entries.extend(load_manifest(manifest_path(root, config)))


def relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def write_markdown_entry(path: Path, frontmatter: dict[str, object], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml_block = dump_yaml(frontmatter, sort_keys=False).strip()
    content = f"---\n{yaml_block}\n---\n"
    if body:
        content += f"\n{body.strip()}\n"
    else:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def write_yaml(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(payload, sort_keys=False), encoding="utf-8")


def load_existing_yaml(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    return data


def write_yaml_if_changed(path: Path, payload: dict[str, object]) -> bool:
    existing = load_existing_yaml(path)
    if existing == payload:
        return False
    write_yaml(path, payload)
    return True


def digest_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def digest_text(text: str) -> str:
    return digest_bytes(text.encode("utf-8"))


def raw_snapshot_path(root: Path, digest: str) -> Path:
    return root / "data" / "raw" / f"{digest}.md"


def write_snapshot(raw_bytes: bytes, root: Path, digest: str) -> Path:
    snapshot_path = raw_snapshot_path(root, digest)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    if not snapshot_path.exists():
        snapshot_path.write_bytes(raw_bytes)
    return snapshot_path


def discover_markdown_files(paths: Sequence[str]) -> list[Path]:
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
        msg = f"capture --from path not found: {raw}"
        raise FileNotFoundError(msg)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in sorted(collected):
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def pending_batches(workspace: Path, config: AppConfig) -> set[Path]:
    """Get all pending profile update batch files."""
    from aijournal.utils.paths import resolve_path

    directory = resolve_path(workspace, config, "derived/pending/profile_updates")
    if not directory.exists():
        return set()
    return {path for path in directory.glob("*.yaml") if path.is_file()}


def noop_preview(
    proposals: Sequence[ClaimProposal],
    claims: Sequence[ClaimAtom],
    timestamp: str,
) -> None:
    del proposals, claims, timestamp


def apply_profile_update_batch(root: Path, config: AppConfig, batch_path: Path) -> bool:
    batch = load_artifact_data(batch_path, ProfileUpdateBatch)
    claim_proposals: list[ClaimProposal] = [
        proposal.model_copy(deep=True) for proposal in batch.proposals.claims
    ]
    facet_proposals: list[FacetChange] = [
        proposal.model_copy(deep=True) for proposal in batch.proposals.facets
    ]

    profile_model, claim_models = load_profile_components(root, config=config)
    profile = profile_to_dict(profile_model)
    claims_data = [claim.model_copy(deep=True) for claim in claim_models]
    timestamp = time_utils.format_timestamp(time_utils.now())

    applied = False
    for claim_proposal in claim_proposals:
        incoming_atom = _proposal_claim_to_atom(claim_proposal, timestamp=timestamp)
        if apply_claim_upsert(claims_data, incoming_atom, timestamp):
            applied = True

    for facet_proposal in facet_proposals:
        if not facet_proposal.path:
            continue
        if apply_profile_update(profile, facet_proposal.path, facet_proposal.value, timestamp):
            applied = True

    if not applied:
        return False

    updated_profile = SelfProfile.model_validate(profile)
    updated_claims = [claim.model_copy(deep=True) for claim in claims_data]
    profile_dir = Path(config.paths.profile)
    if not profile_dir.is_absolute():
        profile_dir = root / profile_dir
    write_yaml_model(profile_dir / "self_profile.yaml", updated_profile)
    write_yaml_model(profile_dir / "claims.yaml", ClaimsFile(claims=updated_claims))
    return True


def _proposal_claim_to_atom(proposal: ClaimProposal, timestamp: str) -> ClaimAtom:
    # Extract claim fields only (exclude proposal metadata)
    claim_payload = proposal.model_dump(
        mode="python",
        exclude={"normalized_ids", "evidence", "manifest_hashes", "rationale"},
    )
    evidence_sources = [
        ClaimSource.model_validate(
            redact_source_text(source).model_dump(mode="python"),
        )
        for source in proposal.evidence
    ]
    claim_payload["provenance"] = {
        "sources": [source.model_dump(mode="python") for source in evidence_sources],
        "first_seen": timestamp.split("T", 1)[0],
        "last_updated": timestamp,
        "observation_count": max(1, len(evidence_sources) or 1),
    }
    return normalization.normalize_claim_atom(
        claim_payload,
        timestamp=timestamp,
        default_sources=evidence_sources,
    )


def ensure_unique_slug(root: Path, date_str: str, base_slug: str) -> str:
    slug = base_slug
    counter = 2
    while journal_path(root, date_str, slug).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def emit_operation_event(
    log_event: Callable[[dict[str, object]], None],
    *,
    event: str,
    status: str,
    result: OperationResult,
    details: dict[str, object] | None = None,
    extra: Mapping[str, object] | None = None,
) -> None:
    """Emit a consistent telemetry payload for non-stage capture events."""
    payload: dict[str, object] = {"event": event, "status": status}
    if result.message:
        payload["message"] = result.message
    payload_details = details if details is not None else result.details
    if payload_details:
        payload["details"] = payload_details
    if result.warnings:
        payload["warnings"] = result.warnings
    if extra:
        payload.update(dict(extra))
    log_event(payload)


def coerce_frontmatter_tags(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw if isinstance(item, (str, int, float))]
    if isinstance(raw, str):
        return [raw]
    return []


def resolve_created_dt(preferred: object, fallback: datetime) -> datetime:
    if preferred:
        result = parse_date_tolerant(preferred, fallback=fallback)
        for warning in result.warnings:
            logger.warning("tolerant date parsing for created_at: %s", warning)
        return result.dt.astimezone(UTC)
    return fallback


def scan_headings(text: str) -> list[dict[str, object]]:
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


def extract_json_frontmatter_block(text: str) -> tuple[str, str]:
    depth = 0
    in_string = False
    escape = False
    start_index = None
    for index, char in enumerate(text):
        if start_index is None:
            if char.isspace():
                continue
            if char != "{":
                msg = "JSON frontmatter must start with '{'"
                raise ValueError(msg)
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
    msg = "Unterminated JSON frontmatter block"
    raise ValueError(msg)


def _extract_json_frontmatter(text: str) -> tuple[dict[str, object], str]:
    block, body = extract_json_frontmatter_block(text)
    try:
        data = json.loads(block) or {}
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        msg = "Invalid JSON frontmatter"
        raise ValueError(msg) from exc
    if not isinstance(data, dict):
        data = {}
    return data, body.lstrip("\n")


def split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    text = strip_invisible_prefix(text)
    stripped = strip_invisible_prefix(text.lstrip())
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


def normalize_markdown(
    markdown_path: Path,
    *,
    root: Path,
    config: AppConfig,
    source_hash: str,
    source_type: str,
) -> tuple[Path, bool]:
    raw_text = markdown_path.read_text(encoding="utf-8")
    try:
        frontmatter, body = split_frontmatter(raw_text)
    except Exception:  # noqa: BLE001 - fallback to tolerant parser
        tolerant = split_frontmatter_tolerant(raw_text)
        frontmatter = tolerant.data
        body = tolerant.body
        for warning in tolerant.warnings:
            logger.warning(
                "tolerant front-matter parsing while normalizing %s: %s",
                markdown_path,
                warning,
            )

    created_dt = resolve_created_dt(frontmatter.get("created_at"), time_utils.now())
    created_str = time_utils.format_timestamp(created_dt)
    date_str = created_dt.strftime("%Y-%m-%d")

    entry_id_raw = frontmatter.get("id") or frontmatter.get("slug")
    if entry_id_raw is None:
        entry_id_raw = markdown_path.stem
    entry_id = str(entry_id_raw)

    title_raw = frontmatter.get("title") or entry_id.replace("-", " ").title()
    title = str(title_raw)

    tags = coerce_frontmatter_tags(frontmatter.get("tags"))
    sections_raw = scan_headings(body)
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
        source_path=relative_path(markdown_path, root),
        title=title,
        tags=tags,
        sections=sections_models,
        summary=summary_text,
        content=body.strip() if body.strip() else None,
        source_hash=source_hash,
        source_type=source_type,
    )
    normalized_path = normalized_entry_path(root, date_str, entry_id, paths=config.paths)
    changed = write_yaml_if_changed(
        normalized_path,
        normalized_entry.model_dump(mode="python"),
    )
    return normalized_path, changed
