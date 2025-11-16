"""Unified profile update command helpers (Prompt 3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from pydantic import BaseModel

from aijournal.commands.facts import _characterization_context, _derived_microfacts_path
from aijournal.commands.ingest import (
    _load_manifest,
    _manifest_path,
    _relative_source_path,
)
from aijournal.commands.profile import (
    load_profile_components,
    profile_to_dict,
)
from aijournal.commands.summarize import (
    _build_meta,
    _derived_summary_path,
    _entries_to_payload,
    _json_block,
    _log_entry_progress,
)
from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.config_loader import load_config, use_fake_llm
from aijournal.common.context import RunContext, create_run_context
from aijournal.common.meta import Artifact, ArtifactKind
from aijournal.domain.facts import DailySummary, MicroFactsFile
from aijournal.domain.journal import NormalizedEntry
from aijournal.domain.prompts import (
    PromptProfileUpdates,
    convert_prompt_updates_to_proposals,
)
from aijournal.io.artifacts import load_artifact_data, save_artifact
from aijournal.io.yaml_io import load_yaml_model
from aijournal.models.derived import ProfileUpdateBatch, ProfileUpdateInput
from aijournal.pipelines import profile_update as profile_update_pipeline
from aijournal.services.microfacts.snapshot import (
    load_consolidated_microfacts,
    select_recurring_facts,
)
from aijournal.services.ollama import LLMResponseError, invoke_structured_llm
from aijournal.services.profile_preview import build_claim_preview
from aijournal.utils import time as time_utils

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aijournal.common.app_config import AppConfig
    from aijournal.domain.changes import ProfileUpdateProposals
    from aijournal.domain.claims import ClaimAtom
    from aijournal.models.authoritative import ManifestEntry

MAX_CONSOLIDATED_FACTS = 20
MIN_CONSOLIDATED_OBSERVATIONS = 2


class ProfileUpdateOptions(BaseModel):
    date: str
    progress: bool


@dataclass(slots=True)
class ProfileUpdatePrepared:
    date: str
    progress: bool
    entries_with_paths: list[tuple[NormalizedEntry, Path]]
    summary: DailySummary | None
    microfacts: MicroFactsFile | None
    consolidated_facts_json: str
    manifest_index: dict[str, ManifestEntry]
    profile: dict[str, Any]
    claim_models: list[ClaimAtom]
    config: AppConfig
    workspace: Path


@dataclass(slots=True)
class ProfileUpdateResult:
    artifact: Artifact[ProfileUpdateBatch]
    path: Path


def run_profile_update(
    date: str,
    *,
    progress: bool,
    workspace: Path | None = None,
    generate_preview: bool = True,
    config: AppConfig | None = None,
) -> Path:
    """Derive profile update batches using the unified prompt."""
    workspace = workspace or Path.cwd()
    config = config or load_config(workspace)
    ctx = create_run_context(
        command="profile.update",
        workspace=workspace,
        config=config,
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )

    options = ProfileUpdateOptions(
        date=date,
        progress=progress,
    )

    return run_profile_update_command(
        ctx,
        options,
        generate_preview=generate_preview,
    )


def run_profile_update_command(
    ctx: RunContext,
    options: ProfileUpdateOptions,
    *,
    generate_preview: bool,
) -> Path:
    def _prepare(_: RunContext, opts: ProfileUpdateOptions) -> ProfileUpdatePrepared:
        entries_with_paths = _load_entries_with_paths(ctx.workspace, ctx.config, opts.date)
        if not entries_with_paths:
            typer.secho(f"No normalized entries for {opts.date}", fg=typer.colors.RED, err=True)
            ctx.emit(event="command_failed", reason="missing_entries")
            raise typer.Exit(1)
        entries = [entry for entry, _ in entries_with_paths]

        timeout_value = ctx.config.llm.timeout
        summary = _load_daily_summary(ctx.workspace, ctx.config, opts.date)
        microfacts = _load_daily_microfacts(ctx.workspace, ctx.config, opts.date)
        manifest_entries = _load_manifest(_manifest_path(ctx.workspace, ctx.config))
        manifest_index = _manifest_by_id(manifest_entries)
        profile_model, claim_models = load_profile_components(ctx.workspace, config=ctx.config)
        profile = profile_to_dict(profile_model)
        claims = [claim.model_copy(deep=True) for claim in claim_models]
        consolidated_facts_json = _load_consolidated_facts_json(ctx.workspace, ctx.config)

        _log_entry_progress(f"Generating profile update for {opts.date}", entries, opts.progress)

        ctx.emit(
            event="prepare_summary",
            entries=len(entries),
            claims=len(claims),
            timeout=timeout_value,
            retries=ctx.config.llm.retries,
            summary=bool(summary),
            microfacts=bool(microfacts),
        )

        return ProfileUpdatePrepared(
            date=opts.date,
            progress=opts.progress,
            entries_with_paths=entries_with_paths,
            summary=summary,
            microfacts=microfacts,
            consolidated_facts_json=consolidated_facts_json,
            manifest_index=manifest_index,
            profile=profile,
            claim_models=claims,
            config=ctx.config,
            workspace=ctx.workspace,
        )

    def _invoke(_: RunContext, prepared: ProfileUpdatePrepared) -> ProfileUpdateResult:
        entries = [entry for entry, _ in prepared.entries_with_paths]
        claim_timestamp = time_utils.format_timestamp(time_utils.now())
        context = _characterization_context(entries, prepared.manifest_index)
        entry_hash_lookup = {
            entry_id: str(entry.hash)
            for entry_id, entry in prepared.manifest_index.items()
            if entry.hash
        }
        summary_json = (
            _json_block(prepared.summary.model_dump(mode="python")) if prepared.summary else "null"
        )
        microfacts_json = (
            _json_block(prepared.microfacts.model_dump(mode="python"))
            if prepared.microfacts
            else "null"
        )
        manifest_payload = _json_block(
            {
                key: entry.model_dump(mode="python")
                for key, entry in prepared.manifest_index.items()
            },
        )

        def request_profile_update() -> ProfileUpdateProposals:
            llm_response = invoke_structured_llm(
                "prompts/profile_update.md",
                {
                    "date": prepared.date,
                    "entries_json": _json_block(_entries_to_payload(entries, prepared.workspace)),
                    "summary_json": summary_json,
                    "microfacts_json": microfacts_json,
                    "consolidated_facts_json": prepared.consolidated_facts_json,
                    "profile_json": _json_block(prepared.profile or {}),
                    "claims_json": _json_block(
                        {
                            "claims": [
                                claim.model_dump(mode="python") for claim in prepared.claim_models
                            ],
                        },
                    ),
                    "manifest_json": manifest_payload,
                },
                response_model=PromptProfileUpdates,
                agent_name="aijournal-profile-update",
                config=prepared.config,
                prompt_set=ctx.prompt_set,
            )
            return convert_prompt_updates_to_proposals(
                llm_response,
                normalized_ids=context[0],
                manifest_hashes=context[1],
                entry_hash_lookup=entry_hash_lookup,
            )

        llm_proposals: ProfileUpdateProposals | None = None
        if not ctx.use_fake_llm:
            llm_proposals = request_profile_update()

        try:
            proposals_model, interview_prompts = profile_update_pipeline.generate_profile_update(
                entries,
                prepared.profile,
                prepared.claim_models,
                use_fake_llm=ctx.use_fake_llm,
                llm_proposals=llm_proposals,
                context=context,
                claim_timestamp=claim_timestamp,
            )
        except LLMResponseError as exc:
            typer.secho(f"Profile update failed: {exc}", fg=typer.colors.RED, err=True)
            ctx.emit(event="command_failed", reason="llm_error", error=str(exc))
            raise typer.Exit(1) from exc

        proposals_model.interview_prompts = interview_prompts
        timestamp = claim_timestamp
        batch_id = f"{prepared.date}-{timestamp}"
        preview_model = (
            build_claim_preview(
                proposals_model.claims,
                prepared.claim_models,
                timestamp=timestamp,
            )
            if generate_preview
            else None
        )
        inputs: list[ProfileUpdateInput] = []
        for entry, path in prepared.entries_with_paths:
            entry_id = entry.id or entry.title or "entry"
            manifest_entry = prepared.manifest_index.get(entry_id)
            manifest_hash = manifest_entry.hash if manifest_entry else None
            normalized_path = _relative_source_path(path, ctx.workspace)
            inputs.append(
                ProfileUpdateInput(
                    id=entry_id,
                    normalized_path=normalized_path,
                    source_hash=entry.source_hash,
                    manifest_hash=str(manifest_hash) if manifest_hash else None,
                    tags=entry.tags or [],
                ),
            )

        batch = ProfileUpdateBatch(
            batch_id=batch_id,
            created_at=timestamp,
            date=prepared.date,
            inputs=inputs,
            proposals=proposals_model,
            preview=preview_model,
        )

        path = _pending_profile_update_path(ctx.workspace, ctx.config, batch_id)
        artifact_meta = _build_meta(
            "prompts/profile_update.md",
            config=prepared.config,
            use_fake_llm=ctx.use_fake_llm,
            prompt_kind="profile_update",
            prompt_set=ctx.prompt_set,
        )
        artifact = Artifact(kind=ArtifactKind.PROFILE_UPDATES, meta=artifact_meta, data=batch)

        ctx.emit(
            event="pipeline_complete",
            claims=len(batch.proposals.claims),
            facets=len(batch.proposals.facets),
            interview_prompts=len(batch.proposals.interview_prompts),
        )
        return ProfileUpdateResult(artifact=artifact, path=path)

    def _persist(_: RunContext, result: ProfileUpdateResult) -> Path:
        result.path.parent.mkdir(parents=True, exist_ok=True)
        save_artifact(result.path, result.artifact)
        return result.path

    return run_command_pipeline(
        ctx,
        options,
        prepare_inputs=_prepare,
        invoke_pipeline=_invoke,
        persist_output=_persist,
    )


def _manifest_by_id(entries: Sequence[ManifestEntry]) -> dict[str, ManifestEntry]:
    index: dict[str, ManifestEntry] = {}
    for entry in entries:
        entry_id = entry.id
        if not entry_id:
            continue
        index[entry_id] = entry
    return index


def _load_daily_summary(workspace: Path, config: AppConfig, day: str) -> DailySummary | None:
    summary_path = _derived_summary_path(workspace, config, day)
    if not summary_path.exists():
        return None
    try:
        return load_artifact_data(summary_path, DailySummary)
    except Exception:  # pragma: no cover - defensive
        return None


def _load_daily_microfacts(workspace: Path, config: AppConfig, day: str) -> MicroFactsFile | None:
    facts_path = _derived_microfacts_path(workspace, config, day)
    if not facts_path.exists():
        return None
    try:
        return load_artifact_data(facts_path, MicroFactsFile)
    except Exception:  # pragma: no cover - defensive
        return None


def _load_entries_with_paths(
    workspace: Path,
    config: AppConfig,
    day: str,
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


def _pending_profile_update_path(workspace: Path, config: AppConfig, batch_id: str) -> Path:
    derived = Path(config.paths.derived)
    if not derived.is_absolute():
        derived = workspace / derived
    pending_dir = derived / "pending" / "profile_updates"
    return pending_dir / f"{_sanitize_batch_id(batch_id)}.yaml"


def _sanitize_batch_id(batch_id: str) -> str:
    """Replace filesystem-hostile characters so pending batches work on Windows."""
    return batch_id.replace(":", "-")


def _load_consolidated_facts_json(workspace: Path, config: AppConfig) -> str:
    """Return JSON payload for recurring consolidated microfacts if available."""
    snapshot = load_consolidated_microfacts(workspace, config)
    if not snapshot:
        return "{}"
    recurring = select_recurring_facts(
        snapshot,
        min_observations=MIN_CONSOLIDATED_OBSERVATIONS,
        limit=MAX_CONSOLIDATED_FACTS,
    )
    if not recurring:
        return "{}"
    return _json_block({"facts": recurring})
