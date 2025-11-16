"""Orchestration helpers for the `aijournal summarize` command."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from string import Template
from typing import Any, TypeVar, cast

import typer
from pydantic import BaseModel

from aijournal.common.app_config import AppConfig
from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.config_loader import load_config, use_fake_llm
from aijournal.common.context import RunContext
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta, LLMResult
from aijournal.domain.facts import DailySummary
from aijournal.domain.journal import NormalizedEntry
from aijournal.io.artifacts import save_artifact
from aijournal.io.yaml_io import load_yaml_model
from aijournal.pipelines import summarize as summarize_pipeline
from aijournal.services.ollama import (
    LLMResponseError,
    build_ollama_config_from_mapping,
    resolve_model_name,
    run_ollama_agent,
)
from aijournal.utils import time as time_utils
from aijournal.utils.paths import resolve_prompt_path

DEFAULT_PROMPTS = {
    "summarize_day.md": (
        "You are a journaling summarizer. Return JSON with day, bullets, highlights, "
        "todo_candidates."
    ),
    "extract_facts.md": 'Extract atomic facts as JSON {"facts":[...]}.',
    "profile_update.md": (
        "Propose JSON with claim/facet updates grounded in entries, summaries, and microfacts."
    ),
    "advise.md": "Return an advice card JSON with recommendations citing facets and claims.",
}

_STRUCTURED_SYSTEM_PROMPT = (
    "You are the summarize agent for the local aijournal CLI. "
    "Read the user's prompt carefully and respond with JSON that matches the declared response schema. "
    "Do not include markdown fences or commentary."
)


class DailySummaryOptions(BaseModel):
    date: str
    progress: bool


@dataclass(slots=True)
class DailySummaryPrepared:
    date: str
    entries: list[NormalizedEntry]
    workspace: Path


@dataclass(slots=True)
class DailySummaryResult:
    summary: DailySummary
    date: str
    model_name: str


StructuredModelT = TypeVar("StructuredModelT", bound=BaseModel)


def _load_prompt_template(prompt_path: str, *, prompt_set: str | None = None) -> str:
    path = resolve_prompt_path(prompt_path, prompt_set=prompt_set)
    if path.exists():
        return path.read_text(encoding="utf-8")
    key = Path(prompt_path).name
    return DEFAULT_PROMPTS.get(prompt_path) or DEFAULT_PROMPTS.get(key, "")


def _render_prompt(
    prompt_path: str, variables: dict[str, str], *, prompt_set: str | None = None
) -> str:
    template = Template(_load_prompt_template(prompt_path, prompt_set=prompt_set))
    return template.safe_substitute(**variables)


def _invoke_structured_llm(
    prompt_path: str,
    variables: dict[str, str],
    *,
    response_model: type[StructuredModelT],
    agent_name: str,
    config: AppConfig,
    prompt_set: str | None = None,
) -> StructuredModelT:
    prompt = _render_prompt(prompt_path, variables, prompt_set=prompt_set)
    prompt_hash = _hash_prompt(prompt_path, prompt_set=prompt_set)
    prompt_kind = Path(prompt_path).stem
    try:
        ollama_config = build_ollama_config_from_mapping(config)

        result: LLMResult[BaseModel] = run_ollama_agent(
            ollama_config,
            prompt,
            system_prompt=_STRUCTURED_SYSTEM_PROMPT,
            output_type=response_model,
            retries=config.llm.retries,
            prompt_path=prompt_path,
            prompt_hash=prompt_hash,
            prompt_kind=prompt_kind,
            prompt_set=prompt_set,
            log_label=agent_name,
        )
        return cast(StructuredModelT, result.payload)
    except Exception as exc:  # pragma: no cover - runtime dependent
        msg = f"Structured output generation failed for {prompt_path}: {exc}"
        raise LLMResponseError(msg) from exc


def _log_entry_progress(action: str, entries: Sequence[NormalizedEntry], enabled: bool) -> None:
    if not enabled:
        return
    total = len(entries)
    plural = "entry" if total == 1 else "entries"
    typer.echo(f"{action}: {total} {plural}")
    if total == 0:
        return
    for idx, entry in enumerate(entries, start=1):
        label = entry.title or entry.id or f"entry-{idx}"
        typer.echo(f"  [{idx}/{total}] {label}")


def _is_timeout_exception(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        message = str(current).lower()
        if isinstance(current, TimeoutError) or "timed out" in message or "timeout" in message:
            return True
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    return False


def _json_block(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _entry_raw_markdown(entry: NormalizedEntry, workspace: Path) -> str | None:
    source = Path(entry.source_path)
    if not source.is_absolute():
        source = workspace / source
    try:
        return source.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _entries_to_payload(
    entries: Sequence[NormalizedEntry],
    workspace: Path,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for entry in entries:
        item = entry.model_dump(mode="python")
        item["raw_markdown"] = _entry_raw_markdown(entry, workspace)
        payloads.append(item)
    return payloads


def _load_normalized_entries(workspace: Path, config: AppConfig, day: str) -> list[NormalizedEntry]:
    data_dir = Path(config.paths.data)
    if not data_dir.is_absolute():
        data_dir = workspace / data_dir
    folder = data_dir / "normalized" / day
    if not folder.exists():
        return []
    entries: list[NormalizedEntry] = []
    for file in sorted(folder.glob("*.yaml")):
        entries.append(load_yaml_model(file, NormalizedEntry))
    return entries


def _derived_summary_path(workspace: Path, config: AppConfig, day: str) -> Path:
    derived = Path(config.paths.derived)
    if not derived.is_absolute():
        derived = workspace / derived
    return derived / "summaries" / f"{day}.yaml"


def _hash_prompt(prompt_path: str, *, prompt_set: str | None = None) -> str | None:
    path = resolve_prompt_path(prompt_path, prompt_set=prompt_set)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    return sha256(data).hexdigest()


def _build_meta(
    prompt_path: str,
    *,
    model: str | None = None,
    config: AppConfig | None = None,
    use_fake_llm: bool,
    prompt_kind: str | None = None,
    prompt_set: str | None = None,
) -> ArtifactMeta:
    resolved_model: str
    if model:
        resolved_model = model
    else:
        resolved_model = resolve_model_name(config, use_fake_llm=use_fake_llm)
    created_at = time_utils.format_timestamp(time_utils.now())
    return ArtifactMeta(
        created_at=created_at,
        model=resolved_model,
        prompt_path=prompt_path,
        prompt_hash=_hash_prompt(prompt_path, prompt_set=prompt_set),
        prompt_kind=prompt_kind,
        prompt_set=prompt_set,
    )


def prepare_inputs(ctx: RunContext, options: DailySummaryOptions) -> DailySummaryPrepared:
    entries = _load_normalized_entries(ctx.workspace, ctx.config, options.date)
    if not entries:
        typer.secho(f"No normalized entries for {options.date}", fg=typer.colors.RED, err=True)
        ctx.emit(event="command_failed", reason="missing_entries")
        raise typer.Exit(1)

    timeout_value = ctx.config.llm.timeout
    retries_value = ctx.config.llm.retries
    _log_entry_progress(
        f"Summarizing entries for {options.date}",
        entries,
        options.progress,
    )
    ctx.emit(
        event="prepare_summary",
        entries=len(entries),
        timeout=timeout_value,
        retries=retries_value,
    )
    return DailySummaryPrepared(
        date=options.date,
        entries=list(entries),
        workspace=ctx.workspace,
    )


def invoke_pipeline(ctx: RunContext, prepared: DailySummaryPrepared) -> DailySummaryResult:
    summary = _summarize_day_payload(
        prepared.entries,
        prepared.date,
        ctx.config,
        workspace=prepared.workspace,
        use_fake_llm_override=ctx.use_fake_llm,
        prompt_set=ctx.prompt_set,
    )
    model_name = resolve_model_name(ctx.config, use_fake_llm=ctx.use_fake_llm)
    ctx.emit(
        event="pipeline_complete",
        bullets=len(summary.bullets),
        highlights=len(summary.highlights),
    )
    return DailySummaryResult(summary=summary, date=prepared.date, model_name=model_name)


def persist_output(ctx: RunContext, result: DailySummaryResult) -> Path:
    summary_path = _derived_summary_path(ctx.workspace, ctx.config, result.date)
    artifact_meta = _build_meta(
        "prompts/summarize_day.md",
        model=result.model_name,
        use_fake_llm=ctx.use_fake_llm,
        prompt_kind="summarize_day",
        prompt_set=ctx.prompt_set,
    )
    artifact = Artifact[DailySummary](
        kind=ArtifactKind.SUMMARY_DAILY,
        meta=artifact_meta,
        data=result.summary,
    )
    save_artifact(summary_path, artifact)
    ctx.emit(event="artifact_written", path=str(summary_path))
    return summary_path


def _summarize_day_payload(
    entries: Sequence[NormalizedEntry],
    date: str,
    config: AppConfig,
    *,
    workspace: Path,
    use_fake_llm_override: bool | None = None,
    prompt_set: str | None = None,
) -> DailySummary:
    fake_mode = use_fake_llm_override if use_fake_llm_override is not None else use_fake_llm()
    llm_summary: DailySummary | None = None
    if not fake_mode:
        llm_summary = cast(
            DailySummary,
            _invoke_structured_llm(
                "prompts/summarize_day.md",
                {
                    "date": date,
                    "entries_json": _json_block(_entries_to_payload(entries, workspace)),
                },
                response_model=DailySummary,
                agent_name="aijournal-summarize",
                config=config,
                prompt_set=prompt_set,
            ),
        )

    return summarize_pipeline.generate_summary(
        entries,
        date,
        use_fake_llm=fake_mode,
        llm_summary=llm_summary,
    )


def run_summarize(
    date: str,
    *,
    progress: bool,
    workspace: Path | None = None,
    config: AppConfig | None = None,
) -> Path:
    """Backward-compatible entrypoint using current working directory."""
    from aijournal.common.context import create_run_context

    workspace = workspace or Path.cwd()
    config = config or load_config(workspace)
    ctx = create_run_context(
        command="summarize",
        workspace=workspace,
        config=config,
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )
    options = DailySummaryOptions(
        date=date,
        progress=progress,
    )
    return run_summarize_command(ctx, options)


def run_summarize_command(ctx: RunContext, options: DailySummaryOptions) -> Path:
    try:
        return run_command_pipeline(
            ctx,
            options,
            prepare_inputs=prepare_inputs,
            invoke_pipeline=invoke_pipeline,
            persist_output=persist_output,
        )
    except LLMResponseError as exc:
        typer.secho(f"Summarize failed: {exc}", fg=typer.colors.RED, err=True)
        ctx.emit(event="command_failed", reason="llm_response_error", error=str(exc))
        raise typer.Exit(1) from exc
