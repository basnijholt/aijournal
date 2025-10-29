"""Characterize command orchestration helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, cast

import typer
from pydantic import BaseModel

from aijournal.commands.facts import _characterization_context, _manifest_by_id
from aijournal.commands.ingest import (
    _load_config,
    _load_manifest,
    _manifest_path,
    _relative_source_path,
    _use_fake_llm,
)
from aijournal.commands.profile import (
    _build_claim_atom_from_entry,
    _load_profile_components,
    _profile_to_dict,
)
from aijournal.commands.summarize import (
    _build_meta,
    _entries_to_payload,
    _invoke_structured_llm,
    _json_block,
    _log_entry_progress,
    _structured_call_with_retry,
)
from aijournal.io.yaml_io import load_yaml_model, write_yaml_model
from aijournal.models import (
    ClaimAtom,
    ClaimProposal,
    ManifestEntry,
    NormalizedEntry,
    ProfileUpdateBatch,
    ProfileUpdateInput,
    ProfileUpdatePreview,
    ProfileUpdateProposals,
)
from aijournal.pipelines import characterize as characterize_pipeline
from aijournal.pipelines import facts as facts_pipeline
from aijournal.services import LLMResponseError
from aijournal.utils import time as time_utils


def run_characterize(
    date: str,
    *,
    timeout: float,
    retries: int,
    progress: bool,
    build_claim_preview: Callable[
        [Sequence[ClaimProposal], Sequence[ClaimAtom], str], ProfileUpdatePreview | None
    ],
    normalize_claims: Callable[..., list[ClaimProposal]] | None = None,
    invoke_structured_llm: Callable[..., BaseModel] = _invoke_structured_llm,
    structured_call: Callable[..., BaseModel] = _structured_call_with_retry,
) -> Path:
    """Derive pending profile updates from normalized entries."""
    if normalize_claims is None:
        normalize_fn = _normalize_claim_proposals
    else:
        normalize_fn = normalize_claims

    root = Path.cwd()
    entries_with_paths = _load_normalized_entries_with_paths(root, date)
    if not entries_with_paths:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    timeout_value = _validate_timeout(timeout)
    manifest_entries = _load_manifest(_manifest_path(root))
    manifest_index = _manifest_by_id(manifest_entries)
    profile_model, claim_models = _load_profile_components(root)
    profile = _profile_to_dict(profile_model)
    config = _load_config(root)

    entries = [entry for entry, _ in entries_with_paths]
    _log_entry_progress(f"Characterizing entries for {date}", entries, progress)
    try:
        proposals_model, interview_prompts = _characterize_payload(
            date,
            entries,
            profile,
            claim_models,
            manifest_index,
            config,
            timeout=timeout_value,
            retries=retries,
            normalize_claims=normalize_fn,
            invoke_structured_llm=invoke_structured_llm,
            structured_call=structured_call,
        )
    except LLMResponseError as exc:
        typer.secho(f"Characterize failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    interview_prompts = facts_pipeline.merge_unique(
        proposals_model.interview_prompts,
        interview_prompts,
    )
    proposals_model.interview_prompts = interview_prompts

    timestamp = time_utils.format_timestamp(time_utils.now())
    batch_id = f"{date}-{timestamp}"

    preview_model = build_claim_preview(
        proposals_model.claims,
        claim_models,
        timestamp,
    )
    if interview_prompts:
        prompts = [prompt for prompt in interview_prompts if prompt]
        if prompts:
            if preview_model is None:
                preview_model = ProfileUpdatePreview(
                    interview_prompts=facts_pipeline.merge_unique([], prompts)
                )
            else:
                preview_model.interview_prompts = facts_pipeline.merge_unique(
                    preview_model.interview_prompts,
                    prompts,
                )

    inputs: list[ProfileUpdateInput] = []
    for data, path in entries_with_paths:
        entry_id = data.id or path.stem
        manifest_entry = manifest_index.get(entry_id)
        manifest_hash = manifest_entry.hash if manifest_entry else None
        inputs.append(
            ProfileUpdateInput(
                id=entry_id,
                normalized_path=_relative_source_path(path, root),
                source_hash=data.source_hash or manifest_hash,
                manifest_hash=manifest_hash,
                tags=list(data.tags or []),
            ),
        )

    meta_model = _build_meta("prompts/characterize.md", config=config)
    batch_model = ProfileUpdateBatch(
        batch_id=batch_id,
        created_at=timestamp,
        date=date,
        inputs=inputs,
        proposals=proposals_model,
        meta=meta_model,
        preview=preview_model,
    )
    pending_dir = _pending_updates_dir(root)
    pending_dir.mkdir(parents=True, exist_ok=True)
    batch_path = _pending_updates_path(root, batch_id)
    write_yaml_model(batch_path, batch_model)
    return batch_path


def _validate_timeout(value: float) -> float:
    if value <= 0:
        typer.secho("--timeout must be positive.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    return value


def _load_normalized_entries_with_paths(root: Path, day: str) -> list[tuple[NormalizedEntry, Path]]:
    folder = root / "data" / "normalized" / day
    if not folder.exists():
        return []
    entries: list[tuple[NormalizedEntry, Path]] = []
    for file in sorted(folder.glob("*.yaml")):
        entries.append((load_yaml_model(file, NormalizedEntry), file))
    return entries


def _pending_updates_dir(root: Path) -> Path:
    return root / "derived" / "pending" / "profile_updates"


def _pending_updates_path(root: Path, batch_id: str) -> Path:
    safe_id = batch_id.replace(":", "-")
    return _pending_updates_dir(root) / f"{safe_id}.yaml"


def _characterize_payload(
    date: str,
    entries: Sequence[NormalizedEntry],
    profile: dict[str, Any],
    claims: Sequence[ClaimAtom],
    manifest_index: dict[str, ManifestEntry],
    config: dict[str, Any],
    *,
    timeout: float | None = None,
    retries: int,
    normalize_claims: Callable[..., list[ClaimProposal]],
    invoke_structured_llm: Callable[..., BaseModel],
    structured_call: Callable[..., BaseModel],
) -> tuple[ProfileUpdateProposals, list[str]]:
    claim_timestamp = time_utils.format_timestamp(time_utils.now())
    context = _characterization_context(entries, manifest_index)
    target_date = date or time_utils.created_date(claim_timestamp)
    manifest_payload = _json_block(
        {key: entry.model_dump(mode="python") for key, entry in manifest_index.items()},
    )

    def request_characterize() -> ProfileUpdateProposals:
        return cast(
            ProfileUpdateProposals,
            invoke_structured_llm(
                "prompts/characterize.md",
                {
                    "date": target_date,
                    "entries_json": _json_block(_entries_to_payload(entries)),
                    "profile_json": _json_block(profile),
                    "claims_json": _json_block(
                        {"claims": [claim.model_dump(mode="python") for claim in claims]}
                    ),
                    "manifest_json": manifest_payload,
                },
                response_model=ProfileUpdateProposals,
                agent_name="aijournal-characterize",
                config=config,
                timeout=timeout,
                max_attempts=max(1, retries + 1),
                retry_message=(
                    "Return JSON with exactly the keys `claims`, `facets`, `interview_prompts`. "
                    "Do not add other keys or narrative text."
                ),
            ),
        )

    return characterize_pipeline.generate_characterization(
        entries,
        profile,
        claims,
        use_fake_llm=_use_fake_llm(),
        structured_call=structured_call,
        request_factory=request_characterize,
        retries=retries,
        label=f"characterize {target_date}",
        context=context,
        claim_timestamp=claim_timestamp,
        build_claim=_build_claim_atom_from_entry,
        normalize_claims=normalize_claims,
        normalize_facets=characterize_pipeline.normalize_facet_proposals,
    )


def _normalize_claim_proposals(
    raw_claims: Iterable[Any],
    *,
    normalized_ids: list[str],
    manifest_hashes: list[str],
    default_sources: Sequence[Any],
    timestamp: str,
) -> list[ClaimProposal]:
    return facts_pipeline.normalize_claim_proposals(
        raw_claims,
        normalized_ids=normalized_ids,
        manifest_hashes=manifest_hashes,
        default_sources=default_sources,
        timestamp=timestamp,
    )


__all__ = [
    "run_characterize",
    "_pending_updates_dir",
    "_pending_updates_path",
    "_normalize_claim_proposals",
]
