"""Orchestration helpers for the `aijournal summarize` command."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from string import Template
from typing import Any, cast

import typer
from pydantic import BaseModel

from aijournal.commands.ingest import _use_fake_llm
from aijournal.common.app_config import AppConfig
from aijournal.common.command_runner import run_command_pipeline
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
from aijournal.utils.paths import WorkspacePaths, resolve_prompt_path

DEFAULT_PROMPTS = {
    "summarize_day.md": (
        "You are a journaling summarizer. Return JSON with day, bullets, highlights, "
        "todo_candidates."
    ),
    "extract_facts.md": 'Extract atomic facts as JSON {"facts":[...]}.',
    "profile_suggest.md": (
        "Propose JSON with upserts and updates grounded in the entries and profile."
    ),
    "advise.md": "Return an advice card JSON with recommendations citing facets and claims.",
    "characterize.md": ("Return JSON with claims and facets describing pending profile updates."),
}

_STRUCTURED_SYSTEM_PROMPT = (
    "You are the summarize agent for the local aijournal CLI. "
    "Read the user's prompt carefully and respond with JSON that matches the declared response schema. "
    "Do not include markdown fences or commentary."
)


class DailySummaryOptions(BaseModel):
    date: str
    timeout: float
    retries: int
    progress: bool


@dataclass(slots=True)
class DailySummaryPrepared:
    date: str
    entries: list[NormalizedEntry]
    timeout: float
    retries: int


@dataclass(slots=True)
class DailySummaryResult:
    summary: DailySummary
    date: str
    model_name: str


def _load_prompt_template(prompt_path: str) -> str:
    path = resolve_prompt_path(prompt_path)
    if path.exists():
        return path.read_text(encoding="utf-8")
    key = Path(prompt_path).name
    return DEFAULT_PROMPTS.get(prompt_path) or DEFAULT_PROMPTS.get(key, "")


def _render_prompt(prompt_path: str, variables: dict[str, str]) -> str:
    template = Template(_load_prompt_template(prompt_path))
    return template.safe_substitute(**variables)


def _invoke_structured_llm(
    prompt_path: str,
    variables: dict[str, str],
    *,
    response_model: type[BaseModel],
    agent_name: str,
    config: AppConfig,
    timeout: float | None = None,
    max_attempts: int = 2,
    retry_message: str | None = None,
) -> BaseModel:
    prompt = _render_prompt(prompt_path, variables)
    prompt_hash = _hash_prompt(prompt_path)
    try:
        ollama_config = build_ollama_config_from_mapping(
            config,
            timeout=float(timeout) if timeout is not None else None,
        )
        effective_retry_message = retry_message or (
            "Return JSON that matches the expected schema with no extra keys or text."
        )
        result: LLMResult[BaseModel] = run_ollama_agent(
            ollama_config,
            prompt,
            system_prompt=_STRUCTURED_SYSTEM_PROMPT,
            output_type=response_model,
            max_attempts=max_attempts,
            retry_message=effective_retry_message,
            prompt_path=prompt_path,
            prompt_hash=prompt_hash,
            log_label=agent_name,
        )
        return cast(BaseModel, result.payload)
    except Exception as exc:  # pragma: no cover - runtime dependent
        msg = f"Structured output generation failed for {prompt_path}: {exc}"
        raise LLMResponseError(msg) from exc


def _validate_timeout(value: float) -> float:
    if value <= 0:
        typer.secho("--timeout must be positive.", fg=typer.colors.RED)
        raise typer.Exit(1)
    return value


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


def _entries_to_payload(entries: Sequence[NormalizedEntry]) -> list[dict[str, Any]]:
    return [entry.model_dump(mode="python") for entry in entries]


def _load_normalized_entries(root: Path, day: str) -> list[NormalizedEntry]:
    del root  # Use WorkspacePaths instead
    folder = WorkspacePaths.data() / "normalized" / day
    if not folder.exists():
        return []
    entries: list[NormalizedEntry] = []
    for file in sorted(folder.glob("*.yaml")):
        entries.append(load_yaml_model(file, NormalizedEntry))
    return entries


def _derived_summary_path(root: Path, day: str) -> Path:
    del root  # Use WorkspacePaths instead
    return WorkspacePaths.derived() / "summaries" / f"{day}.yaml"


def _hash_prompt(prompt_path: str) -> str | None:
    path = resolve_prompt_path(prompt_path)
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
) -> ArtifactMeta:
    resolved_model: str
    if model:
        resolved_model = model
    else:
        resolved_model = resolve_model_name(config, use_fake_llm=_use_fake_llm())
    created_at = time_utils.format_timestamp(time_utils.now())
    return ArtifactMeta(
        created_at=created_at,
        model=resolved_model,
        prompt_path=prompt_path,
        prompt_hash=_hash_prompt(prompt_path),
    )


def prepare_inputs(ctx: RunContext, options: DailySummaryOptions) -> DailySummaryPrepared:
    entries = _load_normalized_entries(ctx.root, options.date)
    if not entries:
        typer.secho(f"No normalized entries for {options.date}", fg=typer.colors.RED, err=True)
        ctx.emit(event="command_failed", reason="missing_entries")
        raise typer.Exit(1)

    timeout_value = _validate_timeout(options.timeout)
    _log_entry_progress(
        f"Summarizing entries for {options.date}",
        entries,
        options.progress,
    )
    ctx.emit(
        event="prepare_summary",
        entries=len(entries),
        timeout=timeout_value,
        retries=options.retries,
    )
    return DailySummaryPrepared(
        date=options.date,
        entries=list(entries),
        timeout=timeout_value,
        retries=options.retries,
    )


def invoke_pipeline(ctx: RunContext, prepared: DailySummaryPrepared) -> DailySummaryResult:
    summary = _summarize_day_payload(
        prepared.entries,
        prepared.date,
        ctx.config,
        timeout=prepared.timeout,
        retries=prepared.retries,
        use_fake_llm=ctx.use_fake_llm,
    )
    model_name = resolve_model_name(ctx.config, use_fake_llm=ctx.use_fake_llm)
    ctx.emit(
        event="pipeline_complete",
        bullets=len(summary.bullets),
        highlights=len(summary.highlights),
    )
    return DailySummaryResult(summary=summary, date=prepared.date, model_name=model_name)


def persist_output(ctx: RunContext, result: DailySummaryResult) -> Path:
    summary_path = _derived_summary_path(ctx.root, result.date)
    artifact_meta = _build_meta("prompts/summarize_day.md", model=result.model_name)
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
    timeout: float | None,
    retries: int,
    invoke_structured_llm: Callable[..., BaseModel] | None = None,
    structured_call: Callable[..., BaseModel] | None = None,
    use_fake_llm: bool | None = None,
) -> DailySummary:
    invoke = invoke_structured_llm or _invoke_structured_llm
    structured = structured_call or (lambda func, *, retries, label: func())
    fake_mode = use_fake_llm if use_fake_llm is not None else _use_fake_llm()

    def request_summary() -> DailySummary:
        return cast(
            DailySummary,
            invoke(
                "prompts/summarize_day.md",
                {
                    "date": date,
                    "entries_json": _json_block(_entries_to_payload(entries)),
                },
                response_model=DailySummary,
                agent_name="aijournal-summarize",
                config=config,
                timeout=timeout,
                max_attempts=max(1, retries + 1),
                retry_message=(
                    "Return JSON with keys `day`, `bullets`, `highlights`, `todo_candidates` "
                    "and no additional fields or commentary."
                ),
            ),
        )

    return summarize_pipeline.generate_summary(
        entries,
        date,
        use_fake_llm=fake_mode,
        structured_call=structured,
        request_factory=request_summary,
        retries=retries,
    )


def run_summarize(
    date: str,
    *,
    timeout: float,
    retries: int,
    progress: bool,
) -> Path:
    """Backward-compatible entrypoint using current working directory."""
    from aijournal.commands.ingest import _load_config, _use_fake_llm
    from aijournal.common.context import create_run_context

    root = Path.cwd()
    config = _load_config(root)
    ctx = create_run_context(
        command="summarize",
        root=root,
        config=config,
        use_fake_llm=_use_fake_llm(),
        trace=False,
        verbose_json=False,
    )
    options = DailySummaryOptions(
        date=date,
        timeout=timeout,
        retries=retries,
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
