"""Characterize command orchestration helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import typer
from pydantic import BaseModel

from aijournal.commands.facts import _characterization_context, _manifest_by_id
from aijournal.commands.ingest import (
    _load_manifest,
    _manifest_path,
    _relative_source_path,
)
from aijournal.commands.profile import (
    _build_claim_atom_from_entry,
    load_profile_components,
    profile_to_dict,
)
from aijournal.commands.summarize import (
    _build_meta,
    _entries_to_payload,
    _invoke_structured_llm,
    _json_block,
    _log_entry_progress,
)
from aijournal.common.app_config import AppConfig
from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.config_loader import load_config, use_fake_llm
from aijournal.common.context import RunContext, create_run_context
from aijournal.common.meta import Artifact, ArtifactKind
from aijournal.domain.changes import (
    ClaimProposal,
    ProfileUpdateProposals,
)
from aijournal.domain.claims import ClaimAtom
from aijournal.domain.journal import NormalizedEntry
from aijournal.io.artifacts import save_artifact
from aijournal.io.yaml_io import load_yaml_model
from aijournal.models.authoritative import ManifestEntry
from aijournal.models.derived import (
    ProfileUpdateBatch,
    ProfileUpdateInput,
    ProfileUpdatePreview,
)
from aijournal.pipelines import characterize as characterize_pipeline
from aijournal.pipelines import facts as facts_pipeline
from aijournal.services.ollama import LLMResponseError
from aijournal.utils import time as time_utils


class CharacterizeOptions(BaseModel):
    date: str
    timeout: float
    retries: int
    progress: bool


@dataclass(slots=True)
class CharacterizePrepared:
    date: str
    timeout: float
    retries: int
    progress: bool
    entries_with_paths: list[tuple[NormalizedEntry, Path]]
    manifest_index: dict[str, ManifestEntry]
    profile: dict[str, Any]
    claim_models: Sequence[ClaimAtom]
    config: AppConfig


@dataclass(slots=True)
class CharacterizeResult:
    artifact: Artifact[ProfileUpdateBatch]
    batch_path: Path


def run_characterize_command(
    ctx: RunContext,
    options: CharacterizeOptions,
    *,
    build_claim_preview: Callable[
        [Sequence[ClaimProposal], Sequence[ClaimAtom], str], ProfileUpdatePreview | None
    ],
    normalize_claims: Callable[..., list[ClaimProposal]],
    invoke_structured_llm: Callable[..., BaseModel],
    structured_call: Callable[..., BaseModel],
) -> Path:
    normalize_fn = normalize_claims if normalize_claims is not None else _normalize_claim_proposals

    def _prepare_inputs(ctx: RunContext, opts: CharacterizeOptions) -> CharacterizePrepared:
        entries_with_paths = _load_normalized_entries_with_paths(
            ctx.workspace, ctx.config, opts.date
        )
        if not entries_with_paths:
            typer.secho(f"No normalized entries for {opts.date}", fg=typer.colors.RED, err=True)
            ctx.emit(event="command_failed", reason="missing_entries")
            raise typer.Exit(1)

        timeout_value = _validate_timeout(opts.timeout)
        manifest_entries = _load_manifest(_manifest_path(ctx.workspace, ctx.config))
        manifest_index = _manifest_by_id(manifest_entries)
        profile_model, claim_models = load_profile_components(ctx.workspace, config=ctx.config)
        profile = profile_to_dict(profile_model)

        entries = [entry for entry, _ in entries_with_paths]
        _log_entry_progress(f"Characterizing entries for {opts.date}", entries, opts.progress)

        ctx.emit(
            event="prepare_summary",
            entry_count=len(entries_with_paths),
            claims=len(claim_models),
        )

        # Replace timeout in options with validated value
        opts.timeout = timeout_value

        return CharacterizePrepared(
            date=opts.date,
            timeout=timeout_value,
            retries=opts.retries,
            progress=opts.progress,
            entries_with_paths=entries_with_paths,
            manifest_index=manifest_index,
            profile=profile,
            claim_models=claim_models,
            config=ctx.config,
        )

    def _invoke_pipeline(ctx: RunContext, prepared: CharacterizePrepared) -> CharacterizeResult:
        entries = [entry for entry, _ in prepared.entries_with_paths]
        try:
            proposals_model, interview_prompts = _characterize_payload(
                prepared.date,
                entries,
                prepared.profile,
                prepared.claim_models,
                prepared.manifest_index,
                prepared.config,
                timeout=prepared.timeout,
                retries=prepared.retries,
                normalize_claims=normalize_fn,
                invoke_structured_llm=invoke_structured_llm,
                structured_call=structured_call,
            )
        except LLMResponseError as exc:
            typer.secho(f"Characterize failed: {exc}", fg=typer.colors.RED, err=True)
            ctx.emit(event="command_failed", reason="llm_error", error=str(exc))
            raise typer.Exit(1)

        interview_prompts = facts_pipeline.merge_unique(
            proposals_model.interview_prompts,
            interview_prompts,
        )
        proposals_model.interview_prompts = interview_prompts

        timestamp = time_utils.format_timestamp(time_utils.now())
        batch_id = f"{prepared.date}-{timestamp}"

        preview_model = build_claim_preview(
            proposals_model.claims,
            prepared.claim_models,
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
        for data, path in prepared.entries_with_paths:
            entry_id = data.id or path.stem
            manifest_entry = prepared.manifest_index.get(entry_id)
            manifest_hash = manifest_entry.hash if manifest_entry else None
            inputs.append(
                ProfileUpdateInput(
                    id=entry_id,
                    normalized_path=_relative_source_path(path, ctx.workspace),
                    source_hash=data.source_hash or manifest_hash,
                    manifest_hash=manifest_hash,
                    tags=list(data.tags or []),
                ),
            )

        artifact_meta = _build_meta(
            "prompts/characterize.md", config=prepared.config, use_fake_llm=ctx.use_fake_llm
        )
        batch_model = ProfileUpdateBatch(
            batch_id=batch_id,
            created_at=timestamp,
            date=prepared.date,
            inputs=inputs,
            proposals=proposals_model,
            preview=preview_model,
        )
        batch_path = _pending_updates_path(ctx.workspace, ctx.config, batch_id)
        artifact = Artifact[ProfileUpdateBatch](
            kind=ArtifactKind.PROFILE_UPDATES,
            meta=artifact_meta,
            data=batch_model,
        )

        ctx.emit(
            event="pipeline_complete",
            claims=len(proposals_model.claims),
            interview_prompts=len(interview_prompts),
            batch_id=batch_id,
        )
        return CharacterizeResult(artifact=artifact, batch_path=batch_path)

    def _persist_output(ctx: RunContext, result: CharacterizeResult) -> Path:
        pending_dir = result.batch_path.parent
        pending_dir.mkdir(parents=True, exist_ok=True)
        save_artifact(result.batch_path, result.artifact)
        return result.batch_path

    return run_command_pipeline(
        ctx,
        options,
        prepare_inputs=_prepare_inputs,
        invoke_pipeline=_invoke_pipeline,
        persist_output=_persist_output,
    )


def run_characterize(
    date: str,
    workspace: Path | None = None,
    *,
    timeout: float,
    retries: int,
    progress: bool,
    build_claim_preview: Callable[
        [Sequence[ClaimProposal], Sequence[ClaimAtom], str], ProfileUpdatePreview | None
    ],
    normalize_claims: Callable[..., list[ClaimProposal]] | None = None,
    invoke_structured_llm: Callable[..., BaseModel] = _invoke_structured_llm,
    structured_call: Callable[..., BaseModel] | None = None,
) -> Path:
    """Derive pending profile updates from normalized entries."""
    if normalize_claims is None:
        normalize_fn = _normalize_claim_proposals
    else:
        normalize_fn = normalize_claims

    workspace = workspace or Path.cwd()
    config = load_config(workspace)
    ctx = create_run_context(
        command="characterize",
        workspace=workspace,
        config=config,
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )

    options = CharacterizeOptions(
        date=date,
        timeout=timeout,
        retries=retries,
        progress=progress,
    )

    structured = structured_call or (lambda func, *, retries, label: func())

    return run_characterize_command(
        ctx,
        options,
        build_claim_preview=build_claim_preview,
        normalize_claims=normalize_fn,
        invoke_structured_llm=invoke_structured_llm,
        structured_call=structured,
    )


def _validate_timeout(value: float) -> float:
    if value <= 0:
        typer.secho("--timeout must be positive.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    return value


def _load_normalized_entries_with_paths(
    workspace: Path, config: AppConfig, day: str
) -> list[tuple[NormalizedEntry, Path]]:
    data_dir = Path(config.paths.data)
    if not data_dir.is_absolute():
        data_dir = workspace / data_dir
    folder = data_dir / "normalized" / day
    if not folder.exists():
        return []
    entries: list[tuple[NormalizedEntry, Path]] = []
    for file in sorted(folder.glob("*.yaml")):
        entries.append((load_yaml_model(file, NormalizedEntry), file))
    return entries


def _pending_updates_dir(workspace: Path, config: AppConfig) -> Path:
    derived = Path(config.paths.derived)
    if not derived.is_absolute():
        derived = workspace / derived
    return derived / "pending" / "profile_updates"


def _pending_updates_path(workspace: Path, config: AppConfig, batch_id: str) -> Path:
    safe_id = batch_id.replace(":", "-")
    return _pending_updates_dir(workspace, config) / f"{safe_id}.yaml"


def _characterize_payload(
    date: str,
    entries: Sequence[NormalizedEntry],
    profile: dict[str, Any],
    claims: Sequence[ClaimAtom],
    manifest_index: dict[str, ManifestEntry],
    config: AppConfig,
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
        use_fake_llm=use_fake_llm(),
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
