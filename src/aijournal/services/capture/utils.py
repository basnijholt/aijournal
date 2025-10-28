"""Shared helper utilities for the capture orchestrator and stages."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import yaml

from aijournal.commands.profile import (
    _apply_claim_upsert,
    _apply_profile_update,
    _load_profile_components,
    _profile_to_dict,
)
from aijournal.io.yaml_io import load_yaml_model, write_yaml_model
from aijournal.models import (
    ClaimAtom,
    ClaimProposal,
    ClaimsFile,
    FacetProposal,
    ManifestEntry,
    ProfileUpdateBatch,
    SelfProfile,
)
from aijournal.types.results import OperationResult
from aijournal.utils import time as time_utils

MARKDOWN_SUFFIXES = {".md", ".markdown"}


def journal_path(root: Path, date_str: str, slug: str) -> Path:
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


def manifest_path(root: Path) -> Path:
    return root / "data" / "manifest" / "ingested.yaml"


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
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def manifest_index(entries: Iterable[ManifestEntry]) -> dict[str, ManifestEntry]:
    return {entry.hash: entry for entry in entries}


def ensure_manifest(entries: list[ManifestEntry], root: Path) -> None:
    if entries:
        return
    entries.extend(load_manifest(manifest_path(root)))


def relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def write_markdown_entry(path: Path, frontmatter: dict[str, object], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml_block = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    content = f"---\n{yaml_block}\n---\n"
    if body:
        content += f"\n{body.strip()}\n"
    else:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def write_yaml(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


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


def use_fake_llm() -> bool:
    return os.getenv("AIJOURNAL_FAKE_OLLAMA") == "1"


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
        raise FileNotFoundError(f"capture --from path not found: {raw}")

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in sorted(collected):
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def pending_batches(root: Path) -> set[Path]:
    directory = root / "derived" / "pending" / "profile_updates"
    if not directory.exists():
        return set()
    return {path for path in directory.glob("*.yaml") if path.is_file()}


def noop_preview(
    proposals: Sequence[ClaimProposal],
    claims: Sequence[ClaimAtom],
    timestamp: str,
) -> None:
    del proposals, claims, timestamp
    return None


def apply_profile_update_batch(root: Path, batch_path: Path) -> bool:
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
