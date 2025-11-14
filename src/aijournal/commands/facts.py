"""Orchestration helpers for the `aijournal facts` command."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import typer
from pydantic import BaseModel, ConfigDict

from aijournal.commands.ingest import (
    _load_manifest,
    _manifest_path,
)
from aijournal.commands.profile import load_profile_components
from aijournal.commands.summarize import (
    _build_meta,
    _entries_to_payload,
    _invoke_structured_llm,
    _json_block,
    _load_normalized_entries,
    _log_entry_progress,
    _validate_timeout,
)
from aijournal.common.app_config import AppConfig
from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.context import RunContext
from aijournal.common.meta import Artifact, ArtifactKind
from aijournal.domain.changes import ClaimProposal
from aijournal.domain.claims import ClaimAtom, ClaimSource
from aijournal.domain.facts import MicroFactsFile
from aijournal.domain.journal import NormalizedEntry
from aijournal.domain.prompts import PromptMicroFacts, convert_prompt_microfacts
from aijournal.io.artifacts import save_artifact
from aijournal.models.authoritative import ManifestEntry
from aijournal.models.derived import ProfileUpdatePreview
from aijournal.pipelines import facts as facts_pipeline
from aijournal.services.ollama import LLMResponseError, resolve_model_name
from aijournal.utils import time as time_utils


def _manifest_by_id(entries: Iterable[ManifestEntry]) -> dict[str, ManifestEntry]:
    index: dict[str, ManifestEntry] = {}
    for entry in entries:
        entry_id = entry.id
        if not entry_id:
            continue
        index[entry_id] = entry
    return index


def _characterization_context(
    entries: Sequence[NormalizedEntry],
    manifest_index: dict[str, ManifestEntry],
) -> tuple[list[str], list[str], list[ClaimSource]]:
    normalized_ids: list[str] = []
    manifest_hashes: set[str] = set()
    default_sources: list[ClaimSource] = []

    for idx, entry in enumerate(entries):
        entry_id = entry.id or f"entry-{idx + 1}"
        normalized_ids.append(entry_id)
        manifest_entry = manifest_index.get(entry_id)
        manifest_hash = manifest_entry.hash if manifest_entry else None
        if manifest_hash:
            manifest_hashes.add(str(manifest_hash))
        default_sources.append(ClaimSource(entry_id=entry_id, spans=[]))

    return normalized_ids, sorted(manifest_hashes), default_sources


def _derived_microfacts_path(workspace: Path, config: AppConfig, day: str) -> Path:
    derived = Path(config.paths.derived)
    if not derived.is_absolute():
        derived = workspace / derived
    return derived / "microfacts" / f"{day}.yaml"


class FactsOptions(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    date: str
    timeout: float
    retries: int
    progress: bool
    claim_models: Sequence[ClaimAtom] | None = None
    preview_builder: (
        Callable[
            [Sequence[ClaimProposal], Sequence[ClaimAtom], str],
            ProfileUpdatePreview | None,
        ]
        | None
    ) = None


@dataclass(slots=True)
class FactsPrepared:
    date: str
    entries: list[NormalizedEntry]
    timeout: float
    retries: int
    manifest_index: dict[str, ManifestEntry]
    claim_models: list[ClaimAtom]
    preview_builder: Callable[
        [Sequence[ClaimProposal], Sequence[ClaimAtom], str],
        ProfileUpdatePreview | None,
    ]
    workspace: Path


@dataclass(slots=True)
class FactsResult:
    microfacts: MicroFactsFile
    preview: ProfileUpdatePreview | None
    date: str


@dataclass(slots=True)
class FactsOutput:
    preview: ProfileUpdatePreview | None
    path: Path


def prepare_inputs(ctx: RunContext, options: FactsOptions) -> FactsPrepared:
    entries = _load_normalized_entries(ctx.workspace, ctx.config, options.date)
    if not entries:
        typer.secho(f"No normalized entries for {options.date}", fg=typer.colors.RED, err=True)
        ctx.emit(event="command_failed", reason="missing_entries")
        raise typer.Exit(1)

    timeout_value = _validate_timeout(options.timeout)
    _log_entry_progress(
        f"Extracting micro-facts for {options.date}",
        entries,
        options.progress,
    )

    manifest_entries = _load_manifest(_manifest_path(ctx.workspace, ctx.config))
    manifest_index = _manifest_by_id(manifest_entries)
    if options.claim_models is not None:
        claim_models = [claim.model_copy(deep=True) for claim in options.claim_models]
    else:
        claim_models = [
            claim.model_copy(deep=True)
            for claim in load_profile_components(ctx.workspace, config=ctx.config)[1]
        ]
    preview_builder = options.preview_builder or (lambda *_args, **_kwargs: None)
    ctx.emit(
        event="prepare_summary",
        entries=len(entries),
        claims=len(claim_models),
        timeout=timeout_value,
        retries=options.retries,
    )
    return FactsPrepared(
        date=options.date,
        entries=list(entries),
        timeout=timeout_value,
        retries=options.retries,
        manifest_index=manifest_index,
        claim_models=claim_models,
        preview_builder=preview_builder,
        workspace=ctx.workspace,
    )


def invoke_pipeline(ctx: RunContext, prepared: FactsPrepared) -> FactsResult:
    context = _characterization_context(prepared.entries, prepared.manifest_index)

    def request_microfacts() -> MicroFactsFile:
        llm_response = cast(
            PromptMicroFacts,
            _invoke_structured_llm(
                "prompts/extract_facts.md",
                {
                    "date": prepared.date,
                    "entries_json": _json_block(
                        _entries_to_payload(prepared.entries, prepared.workspace)
                    ),
                },
                response_model=PromptMicroFacts,
                agent_name="aijournal-facts",
                config=ctx.config,
                timeout=prepared.timeout,
                max_attempts=max(1, prepared.retries + 1),
                retry_message=(
                    "Return JSON with keys `facts`, `claim_proposals`, and optional `preview`."
                ),
            ),
        )
        return convert_prompt_microfacts(llm_response)

    facts_data = facts_pipeline.generate_microfacts(
        prepared.entries,
        prepared.date,
        use_fake_llm=ctx.use_fake_llm,
        structured_call=lambda func, *, retries, label: func(),
        request_factory=request_microfacts,
        retries=prepared.retries,
        context=context,
        manifest_index=prepared.manifest_index,
    )

    preview = prepared.preview_builder(
        facts_data.claim_proposals,
        [claim.model_copy(deep=True) for claim in prepared.claim_models],
        time_utils.format_timestamp(time_utils.now()),
    )
    facts_data.preview = preview
    ctx.emit(
        event="pipeline_complete",
        facts=len(facts_data.facts),
        claim_proposals=len(facts_data.claim_proposals),
    )
    return FactsResult(microfacts=facts_data, preview=preview, date=prepared.date)


def persist_output(ctx: RunContext, result: FactsResult) -> FactsOutput:
    facts_path = _derived_microfacts_path(ctx.workspace, ctx.config, result.date)
    model_name = resolve_model_name(ctx.config, use_fake_llm=ctx.use_fake_llm)
    artifact_meta = _build_meta(
        "prompts/extract_facts.md", model=model_name, use_fake_llm=ctx.use_fake_llm
    )
    save_artifact(
        facts_path,
        Artifact[MicroFactsFile](
            kind=ArtifactKind.MICROFACTS_DAILY,
            meta=artifact_meta,
            data=result.microfacts,
        ),
    )
    ctx.emit(event="artifact_written", path=str(facts_path))
    return FactsOutput(preview=result.preview, path=facts_path)


def run_facts_command(ctx: RunContext, options: FactsOptions) -> FactsOutput:
    try:
        return run_command_pipeline(
            ctx,
            options,
            prepare_inputs=prepare_inputs,
            invoke_pipeline=invoke_pipeline,
            persist_output=persist_output,
        )
    except LLMResponseError as exc:
        typer.secho(f"Facts extraction failed: {exc}", fg=typer.colors.RED, err=True)
        ctx.emit(event="command_failed", reason="llm_response_error", error=str(exc))
        raise typer.Exit(1) from exc


def run_facts(
    date: str,
    *,
    timeout: float,
    retries: int,
    progress: bool,
    claim_models: Sequence[ClaimAtom],
    build_claim_preview: Callable[
        [Sequence[ClaimProposal], Sequence[ClaimAtom], str], ProfileUpdatePreview | None
    ],
    workspace: Path | None = None,
) -> tuple[ProfileUpdatePreview | None, Path]:
    from aijournal.common.config_loader import load_config, use_fake_llm
    from aijournal.common.context import create_run_context

    workspace = workspace or Path.cwd()
    config = load_config(workspace)
    ctx = create_run_context(
        command="facts",
        workspace=workspace,
        config=config,
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )
    options = FactsOptions(
        date=date,
        timeout=timeout,
        retries=retries,
        progress=progress,
        claim_models=claim_models,
        preview_builder=build_claim_preview,
    )
    output = run_facts_command(ctx, options)
    return output.preview, output.path
