"""Orchestration helpers for the `aijournal summarize` command."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from hashlib import sha256
from pathlib import Path
from string import Template
from typing import Any, cast

import typer
from pydantic import BaseModel

from aijournal.commands.ingest import _load_config, _use_fake_llm
from aijournal.io.yaml_io import load_yaml_model, write_yaml_model
from aijournal.models import DailySummaryResponse, NormalizedEntry, SummaryMeta
from aijournal.pipelines import summarize as summarize_pipeline
from aijournal.services import LLMResponseError, build_ollama_config_from_mapping, run_ollama_agent
from aijournal.utils import time as time_utils
from aijournal.utils.paths import resolve_prompt_path

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
    "You are part of the local aijournal CLI. "
    "Read the user's prompt carefully and respond with JSON that matches the declared response schema. "
    "Do not include markdown fences or commentary."
)


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
    config: dict[str, Any],
    timeout: float | None = None,
) -> BaseModel:
    prompt = _render_prompt(prompt_path, variables)
    try:
        ollama_config = build_ollama_config_from_mapping(
            config,
            timeout=float(timeout) if timeout is not None else None,
        )
        return run_ollama_agent(
            ollama_config,
            prompt,
            system_prompt=_STRUCTURED_SYSTEM_PROMPT,
            output_type=response_model,
        )
    except Exception as exc:  # pragma: no cover - runtime dependent
        msg = f"Structured output generation failed for {prompt_path}: {exc}"
        raise LLMResponseError(msg) from exc


def _validate_timeout(value: float) -> float:
    if value <= 0:
        typer.secho("--timeout must be positive.", fg=typer.colors.RED, err=True)
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


def _structured_call_with_retry(
    func: Callable[[], BaseModel],
    *,
    retries: int,
    label: str,
) -> BaseModel:
    attempts_used = 0
    total_attempts = max(1, retries + 1)
    while True:
        try:
            return func()
        except LLMResponseError as exc:
            if attempts_used >= retries:
                raise
            attempts_used += 1
            reason = "timeout" if _is_timeout_exception(exc) else "schema error"
            next_attempt = attempts_used + 1
            typer.secho(
                f"{label}: retrying after {reason} (attempt {next_attempt}/{total_attempts}).",
                fg=typer.colors.YELLOW,
                err=True,
            )


def _json_block(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _entries_to_payload(entries: Sequence[NormalizedEntry]) -> list[dict[str, Any]]:
    return [entry.model_dump(mode="python") for entry in entries]


def _load_normalized_entries(root: Path, day: str) -> list[NormalizedEntry]:
    folder = root / "data" / "normalized" / day
    if not folder.exists():
        return []
    entries: list[NormalizedEntry] = []
    for file in sorted(folder.glob("*.yaml")):
        entries.append(load_yaml_model(file, NormalizedEntry))
    return entries


def _derived_summary_path(root: Path, day: str) -> Path:
    return root / "derived" / "summaries" / f"{day}.yaml"


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
    config: dict[str, Any] | None = None,
) -> SummaryMeta:
    resolved_model: str
    if model:
        resolved_model = model
    else:
        config_payload = config if isinstance(config, dict) else {}
        resolved_model = (
            "fake-ollama"
            if _use_fake_llm()
            else build_ollama_config_from_mapping(config_payload).model
        )
    return SummaryMeta(
        llm_model=resolved_model,
        prompt_path=prompt_path,
        prompt_hash=_hash_prompt(prompt_path),
        created_at=time_utils.format_timestamp(time_utils.now()),
    )


def run_summarize(
    date: str,
    *,
    timeout: float,
    retries: int,
    progress: bool,
) -> Path:
    """Generate a daily summary and return the output path."""
    root = Path.cwd()
    entries = _load_normalized_entries(root, date)
    if not entries:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    timeout_value = _validate_timeout(timeout)
    _log_entry_progress(f"Summarizing entries for {date}", entries, progress)

    config = _load_config(root)
    use_fake_llm = _use_fake_llm()

    def request_summary() -> DailySummaryResponse:
        return cast(
            DailySummaryResponse,
            _invoke_structured_llm(
                "prompts/summarize_day.md",
                {"date": date, "entries_json": _json_block(_entries_to_payload(entries))},
                response_model=DailySummaryResponse,
                agent_name="aijournal-summarize",
                config=config,
                timeout=timeout_value,
            ),
        )

    try:
        summary_data = summarize_pipeline.generate_summary(
            entries,
            date,
            use_fake_llm=use_fake_llm,
            structured_call=_structured_call_with_retry,
            request_factory=request_summary,
            retries=retries,
        )
    except LLMResponseError as exc:
        typer.secho(f"Summarize failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    summary_data.meta = _build_meta("prompts/summarize_day.md", config=config)
    summary_path = _derived_summary_path(root, date)
    write_yaml_model(summary_path, summary_data)
    return summary_path
