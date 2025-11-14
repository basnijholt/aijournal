"""Typer CLI entrypoint for aijournal.

This module intentionally keeps only Typer glue and lightweight interactive
helpers. Command orchestration now lives under ``aijournal.commands``; any
remaining utilities here support interactive previews that still require direct
terminal IO.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import click
import httpx
import typer
from pydantic import ValidationError
from typer.models import CommandInfo

from aijournal.api.capture import CaptureRequest
from aijournal.commands import summarize as summarize_commands
from aijournal.commands.advise import (
    AdviceOptions,
    _collect_pending_interview_prompts,
    run_advise_command,
)
from aijournal.commands.audit import run_audit_provenance_cli
from aijournal.commands.characterize import (
    _normalize_claim_proposals,
    _pending_updates_dir,
    run_characterize,
)
from aijournal.commands.chat import run_chat
from aijournal.commands.chatd import run_chatd
from aijournal.commands.facts import (
    FactsOptions,
    run_facts_command,
)
from aijournal.commands.index import (
    run_index_rebuild,
    run_index_search,
    run_index_tail,
)
from aijournal.commands.ingest import (
    _parse_entry,
    _relative_source_path,
    _write_yaml_if_changed,
    run_ingest,
)
from aijournal.commands.init import run_init
from aijournal.commands.new import run_new
from aijournal.commands.pack import run_pack
from aijournal.commands.persona import persona_state, run_persona_build
from aijournal.commands.profile import (
    InterviewTarget,
    _compute_rankings,
    apply_claim_upsert,
    apply_profile_update,
    load_profile_components,
    profile_to_dict,
    run_profile_apply,
    run_profile_status,
    run_profile_suggest,
)
from aijournal.commands.summarize import (
    DailySummaryOptions,
    _entries_to_payload,
    _json_block,
    _load_normalized_entries,
    run_summarize_command,
)
from aijournal.commands.summarize import (
    _invoke_structured_llm as _commands_invoke_structured_llm,
)
from aijournal.commands.system import run_system_doctor_cli, run_system_status_cli
from aijournal.common.app_config import AppConfig
from aijournal.common.config_loader import load_config, use_fake_llm
from aijournal.common.constants import (
    DEFAULT_LLM_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
)
from aijournal.common.context import RunContext, create_run_context
from aijournal.domain.changes import ClaimProposal, FacetChange
from aijournal.domain.claims import ClaimAtom, ClaimSource, Scope
from aijournal.domain.events import (
    ClaimConflictPayload,
    ClaimEventAction,
    ClaimPreviewEvent,
    ClaimSignaturePayload,
    FeedbackBatch,
)
from aijournal.domain.evidence import redact_source_text
from aijournal.domain.journal import NormalizedEntry
from aijournal.domain.persona import InterviewQuestion, InterviewSet
from aijournal.io.artifacts import load_artifact_data
from aijournal.io.yaml_io import dump_yaml, load_yaml_model, write_yaml_model
from aijournal.models.authoritative import ClaimsFile, SelfProfile
from aijournal.models.derived import ProfileUpdateBatch, ProfileUpdatePreview
from aijournal.pipelines import normalization
from aijournal.services.capture import (
    CAPTURE_MAX_STAGE,
    CAPTURE_STAGES,
    CaptureInput,
    run_capture,
)
from aijournal.services.consolidator import (
    ClaimConflict,
    ClaimConsolidator,
    ClaimMergeOutcome,
    ClaimSignature,
)
from aijournal.services.ollama import (
    LLMResponseError,
    build_ollama_config_from_mapping,
    resolve_ollama_host,
    run_ollama_agent,
)
from aijournal.services.persona_export import (
    PersonaArtifactMissingError,
    PersonaContentError,
    PersonaExportResult,
    PersonaVariant,
    export_persona_markdown,
    load_persona_core,
)
from aijournal.services.persona_export import (
    PersonaExportOptions as PersonaExportOptions,
)
from aijournal.utils import time as time_utils
from aijournal.utils.coercion import coerce_int
from aijournal.utils.paths import (
    find_data_root,
    normalized_entry_path,
)


def _get_workspace() -> Path:
    """Get workspace directory from environment or default to current directory.

    Checks AIJOURNAL_WORKSPACE environment variable first, falls back to cwd.
    Expands ~ and resolves relative paths, then validates that the directory
    exists and contains a config.yaml file.

    Raises:
        RuntimeError: If the workspace directory doesn't exist, is not a directory,
                     or doesn't contain config.yaml
    """
    workspace_env = os.getenv("AIJOURNAL_WORKSPACE")
    if workspace_env:
        # Expand ~ and make path absolute for better error messages
        workspace = Path(workspace_env).expanduser().resolve()
    else:
        workspace = Path.cwd()

    # Check workspace directory exists
    if not workspace.exists():
        msg = (
            f"Workspace directory does not exist: {workspace}\n"
            f"Run 'aijournal init --path {workspace}' to create it"
        )
        raise RuntimeError(msg)

    # Check it's actually a directory
    if not workspace.is_dir():
        msg = f"Workspace path is not a directory: {workspace}"
        raise RuntimeError(msg)

    # Check for config.yaml
    config_path = workspace / "config.yaml"
    if not config_path.exists():
        msg = (
            f"Not an aijournal workspace: {workspace}\n"
            f"Missing config.yaml - run 'aijournal init --path {workspace}' first"
        )
        raise RuntimeError(msg)

    return workspace


app = typer.Typer(
    help="Local-first personal journal utilities.",
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
    no_args_is_help=True,
)
profile_app = typer.Typer(help="Profile utilities.")
ollama_app = typer.Typer(help="Ollama helpers.")
index_app = typer.Typer(help="Retrieval index utilities.")
persona_app = typer.Typer(help="Persona utilities.")

# Phase 1 scaffold: advanced operations namespace and placeholder groups.
ops_app = typer.Typer(help="Advanced operations namespace.")
ops_pipeline_app = typer.Typer(help="Pipeline tools (normalize, summarize, characterize).")
ops_feedback_app = typer.Typer(help="Feedback processing utilities.")
ops_logs_app = typer.Typer(help="Log utilities.")
ops_system_app = typer.Typer(help="System diagnostics and doctor helpers.")
ops_dev_app = typer.Typer(help="Developer fixtures and helpers.")
ops_audit_app = typer.Typer(help="Audit and governance utilities.")

ops_app.add_typer(ops_pipeline_app, name="pipeline")
ops_app.add_typer(profile_app, name="profile")
ops_app.add_typer(index_app, name="index")
ops_app.add_typer(persona_app, name="persona")
ops_app.add_typer(ops_feedback_app, name="feedback")
ops_app.add_typer(ops_logs_app, name="logs")
ops_app.add_typer(ops_system_app, name="system")
ops_app.add_typer(ops_dev_app, name="dev")
ops_app.add_typer(ops_audit_app, name="audit")

ops_system_app.add_typer(ollama_app, name="ollama")

app.add_typer(ops_app, name="ops")

export_app = typer.Typer(help="Context export utilities.")
serve_app = typer.Typer(help="Service runners and daemons.")

app.add_typer(export_app, name="export")
app.add_typer(serve_app, name="serve")


@dataclass
class CLISettings:
    trace: bool = False
    verbose_json: bool = False


@app.callback()
def _main_callback(
    ctx: typer.Context,
    trace: bool = typer.Option(
        False,
        "--trace",
        help="Mirror structured trace events to stdout.",
    ),
    verbose_json: bool = typer.Option(
        False,
        "--verbose-json",
        help="Mirror structured trace events as JSON to stdout.",
    ),
) -> None:
    ctx.obj = CLISettings(trace=trace, verbose_json=verbose_json)


def _cli_settings() -> CLISettings:
    context = click.get_current_context(silent=True)
    while context is not None:
        obj = getattr(context, "obj", None)
        if isinstance(obj, CLISettings):
            return obj
        context = context.parent
    settings = CLISettings()
    context = click.get_current_context(silent=True)
    if context is not None:
        context.obj = settings
    return settings


def _run_context(
    command: str,
    *,
    workspace: Path | None = None,
    config: AppConfig | None = None,
) -> RunContext:
    """Create a run context for command execution.

    Args:
        command: Command name
        workspace: Workspace directory (defaults to _get_workspace())

    Returns:
        Configured RunContext
    """
    settings = _cli_settings()
    actual_workspace = workspace or _get_workspace()
    config_model = config or load_config(actual_workspace)
    return create_run_context(
        command=command,
        workspace=actual_workspace,
        config=config_model,
        use_fake_llm=use_fake_llm(),
        trace=settings.trace,
        verbose_json=settings.verbose_json,
    )


CAPTURE_STAGE_LOOKUP = {stage.stage_id: stage for stage in CAPTURE_STAGES}
CAPTURE_STAGE_TABLE = "\n".join(
    f"[{stage.stage_id}] {stage.name} – {stage.description}" for stage in CAPTURE_STAGES
)


def _emit_deprecation(command: str, replacement: str | None = None) -> None:
    """Emit a standardized deprecation notice for legacy commands."""
    message = f"[DEPRECATED] `{command}` has moved into the new capture-first workflow."
    if replacement:
        message += f" Use `{replacement}` instead."
    typer.secho(message, fg=typer.colors.YELLOW, err=True)


def _format_trace_line(payload: dict[str, Any]) -> tuple[str, str | None]:
    timestamp = payload.get("timestamp", "?")
    run_id = payload.get("run_id")
    command = payload.get("command", "")
    event = payload.get("event", "")
    step = payload.get("step")
    duration = payload.get("duration_ms")
    error = payload.get("error")

    parts: list[str] = [str(timestamp)]
    if run_id:
        parts.append(f"#{run_id}")
    if command:
        parts.append(str(command))
    if event:
        parts.append(str(event))
    if step:
        parts.append(f"step={step}")
    if isinstance(duration, (int, float)):
        parts.append(f"{duration:.1f}ms")
    elif isinstance(duration, str) and duration:
        parts.append(duration)
    if error:
        parts.append(f"error={error}")

    color: str | None = None
    if error or event == "error":
        color = typer.colors.RED
    elif event == "start":
        color = typer.colors.BLUE
    elif event == "end":
        color = typer.colors.GREEN

    message = " | ".join(part for part in parts if part)
    return message, color


@ops_logs_app.command("tail")
def logs_tail(
    last: int = typer.Option(
        10,
        "--last",
        "-n",
        min=1,
        help="Number of recent trace events to display.",
    ),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Emit raw JSON lines instead of formatted output.",
    ),
    path: Path | None = typer.Option(
        None,
        "--path",
        help="Override trace log path (defaults to derived/logs/run_trace.jsonl).",
    ),
) -> None:
    """Show the most recent structured trace events."""

    workspace = _get_workspace()
    log_path = path or workspace / "derived" / "logs" / "run_trace.jsonl"
    if not log_path.exists():
        typer.secho(f"No run trace log found at {log_path}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        typer.secho("Log file is empty.", fg=typer.colors.YELLOW)
        return

    slice_start = max(0, len(lines) - last)
    for line in lines[slice_start:]:
        if raw:
            typer.echo(line)
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            typer.echo(line)
            continue
        message, color = _format_trace_line(payload)
        if color:
            typer.secho(message, fg=color)
        else:
            typer.echo(message)


@app.command(help="Capture Markdown into the journal workspace and refresh derived artifacts.")
def capture(
    from_paths: list[Path] | None = typer.Option(
        None,
        "--from",
        help="File or directory to import (repeatable).",
        exists=True,
        dir_okay=True,
        file_okay=True,
        readable=True,
        resolve_path=True,
        rich_help_panel="INPUT",
    ),
    text: str | None = typer.Option(
        None,
        "--text",
        help="Raw Markdown content to capture directly from the CLI.",
        rich_help_panel="INPUT",
    ),
    snapshot: bool = typer.Option(
        True,
        "--snapshot/--no-snapshot",
        help="Store raw copies under data/raw/<hash>.md when importing files.",
        rich_help_panel="IMPORT BEHAVIOR",
    ),
    source_type: str = typer.Option(
        "journal",
        "--source-type",
        help="Semantic classification recorded in front matter (journal|notes|blog).",
        rich_help_panel="METADATA",
    ),
    date: str | None = typer.Option(
        None,
        "--date",
        "-d",
        help="Fallback created_at date when input lacks one (YYYY-MM-DD).",
        rich_help_panel="METADATA",
    ),
    title: str | None = typer.Option(
        None,
        "--title",
        help="Override title when capturing raw text.",
        rich_help_panel="METADATA",
    ),
    tags: list[str] = typer.Option(
        [],
        "--tag",
        "-t",
        help="Tag to merge into front matter (repeatable).",
        rich_help_panel="METADATA",
        show_default=False,
    ),
    projects: list[str] = typer.Option(
        [],
        "--project",
        help="Project to merge into front matter (repeatable).",
        rich_help_panel="METADATA",
        show_default=False,
    ),
    mood: str | None = typer.Option(
        None,
        "--mood",
        help="Mood value to record in front matter.",
        rich_help_panel="METADATA",
    ),
    apply_profile: str = typer.Option(
        "auto",
        "--apply-profile",
        help="Apply profile suggestions automatically or leave for review (auto|review).",
        rich_help_panel="APPLY & REFRESH",
    ),
    rebuild: str = typer.Option(
        "auto",
        "--rebuild",
        help="Rebuild persona/index artifacts (auto|always|skip).",
        rich_help_panel="APPLY & REFRESH",
    ),
    pack: str | None = typer.Option(
        None,
        "--pack",
        help="Emit a context pack level when persona changes (L1|L3|L4).",
        rich_help_panel="APPLY & REFRESH",
    ),
    min_stage: int = typer.Option(
        0,
        "--min-stage",
        help=f"Lowest capture stage (0-{CAPTURE_MAX_STAGE}) to execute; capture always revalidates stages 0-1.",
        rich_help_panel="STAGE CONTROL",
    ),
    max_stage: int = typer.Option(
        CAPTURE_MAX_STAGE,
        "--max-stage",
        help=f"Highest capture stage (0-{CAPTURE_MAX_STAGE}) to execute. Stages:\n{CAPTURE_STAGE_TABLE}",
        rich_help_panel="STAGE CONTROL",
    ),
    retries: int = typer.Option(
        DEFAULT_LLM_RETRIES,
        "--retries",
        min=0,
        help="Structured-output retry attempts per stage.",
        rich_help_panel="LLM & VALIDATION",
    ),
    progress: bool = typer.Option(
        True,
        "--progress/--no-progress",
        help="Show per-stage progress indicators during derivations.",
        rich_help_panel="LLM & VALIDATION",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Skip writes and report planned actions only.",
        rich_help_panel="APPLY & REFRESH",
    ),
) -> None:
    """Persist new material and refresh downstream artifacts in one pass."""

    stdin_text: str | None = None
    if not from_paths and text is None and not sys.stdin.isatty():
        stdin_buffer = sys.stdin.read()
        if stdin_buffer and stdin_buffer.strip():
            stdin_text = stdin_buffer

    effective_text = text if text is not None else stdin_text

    if bool(from_paths) and effective_text:
        typer.secho("Provide either --from or --text, not both.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    if not from_paths and not effective_text:
        typer.secho(
            "Use --from to import files/directories or --text for raw Markdown.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    source_type_value = source_type.lower()
    if source_type_value not in {"journal", "notes", "blog"}:
        typer.secho(
            "--source-type must be one of: journal, notes, blog.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)

    apply_profile_value = apply_profile.lower()
    if apply_profile_value not in {"auto", "review"}:
        typer.secho("--apply-profile must be auto or review.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    rebuild_value = rebuild.lower()
    if rebuild_value not in {"auto", "always", "skip"}:
        typer.secho("--rebuild must be auto, always, or skip.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    pack_value: str | None = None
    if pack:
        pack_upper = pack.upper()
        if pack_upper not in {"L1", "L3", "L4"}:
            typer.secho("--pack must be one of: L1, L3, L4.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        pack_value = pack_upper

    if not (0 <= min_stage <= CAPTURE_MAX_STAGE and 0 <= max_stage <= CAPTURE_MAX_STAGE):
        typer.secho(
            f"--min-stage/--max-stage must be between 0 and {CAPTURE_MAX_STAGE}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    if min_stage > max_stage:
        typer.secho(
            "--min-stage cannot be greater than --max-stage.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)

    if from_paths:
        resolved_paths = [str(path.resolve()) for path in from_paths]
        contains_dir = any(path.is_dir() for path in from_paths)
        source_mode: Literal["stdin", "editor", "file", "dir"] = "dir" if contains_dir else "file"
    else:
        resolved_paths = []
        source_mode = "stdin"

    capture_request = CaptureRequest(
        source=source_mode,
        text=effective_text,
        paths=resolved_paths,
        source_type=source_type_value,  # type: ignore[arg-type]
        date=date,
        title=title,
        slug=None,
        tags=tags,
        projects=projects,
        mood=mood,
        apply_profile=apply_profile_value,  # type: ignore[arg-type]
        rebuild=rebuild_value,  # type: ignore[arg-type]
        pack=pack_value,  # type: ignore[arg-type]
        retries=retries,
        progress=progress,
        dry_run=dry_run,
        snapshot=snapshot,
    )

    capture_input = CaptureInput.from_request(
        capture_request,
        min_stage=min_stage,
        max_stage=max_stage,
    )

    result = run_capture(capture_input)

    if result.errors:
        for error in result.errors:
            typer.secho(error, fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    for warning in result.warnings:
        typer.secho(warning, fg=typer.colors.YELLOW, err=False)

    created = [entry for entry in result.entries if entry.changed and not entry.deduped]
    deduped = [entry for entry in result.entries if entry.deduped]

    if created:
        typer.secho("Captured entries:", fg=typer.colors.GREEN)
        for entry in created:
            typer.echo(f"  - {entry.date} / {entry.slug}")
    if deduped:
        typer.secho("Skipped duplicates:", fg=typer.colors.BLUE)
        for entry in deduped:
            typer.echo(f"  - {entry.date} / {entry.slug}")

    completed_set = set(result.stages_completed)
    if completed_set:
        typer.secho("Stages completed:", fg=typer.colors.GREEN)
        for idx in sorted(completed_set):
            stage = CAPTURE_STAGE_LOOKUP.get(idx)
            if stage:
                typer.echo(f"  [{idx}] {stage.name}")

    requested_range = range(result.min_stage, result.max_stage + 1)
    pending = [idx for idx in requested_range if idx not in completed_set]
    if pending:
        typer.secho("Requested stages pending manual follow-up:", fg=typer.colors.YELLOW)
        for idx in pending:
            stage = CAPTURE_STAGE_LOOKUP.get(idx)
            if not stage:
                continue
            manual = stage.manual.replace("\n", "\n    ")
            typer.echo(f"  [{idx}] {stage.name} – {stage.description}\n    {manual}")

    if result.max_stage < CAPTURE_MAX_STAGE:
        typer.secho("Additional stages not requested in this run:", fg=typer.colors.BLUE)
        for idx in range(result.max_stage + 1, CAPTURE_MAX_STAGE + 1):
            stage = CAPTURE_STAGE_LOOKUP.get(idx)
            if not stage:
                continue
            manual = stage.manual.replace("\n", "\n    ")
            typer.echo(f"  [{idx}] {stage.name} – {stage.description}\n    {manual}")

    typer.echo(
        json.dumps(
            {
                "run_id": result.run_id,
                "entries": len(result.entries),
                "created": len(created),
                "deduped": len(deduped),
            },
            indent=2,
        )
    )


@app.command()
def status() -> None:
    """Display persona, index, and retrieval freshness."""
    run_system_status_cli()


@ops_system_app.command("doctor")
def system_doctor() -> None:
    """Run system diagnostics and emit machine-readable results."""
    run_system_doctor_cli()


@ops_audit_app.command("provenance")
def audit_provenance_command(
    fix: bool = typer.Option(
        False,
        "--fix/--no-fix",
        help="Redact span.text fields when present instead of only reporting them.",
    ),
) -> None:
    """Scan claims and derived artifacts for span.text remnants."""
    run_audit_provenance_cli(fix=fix)


@app.callback()
def main() -> None:
    """Aijournal command-line interface."""
    # Intentionally empty; commands provide functionality.
    return


HIGH_IMPACT_PROBES = [
    "- Top 3 values you refuse to trade off—rank them.",
    "- One long-term goal that matters most this year—and why now?",
    "- When speed and quality conflict, what do you choose by default?",
    "- List 2 anti-goals (things you want to avoid) and the reasons.",
    "- Your risk posture in career moves: low / medium / high—why?",
    "- Energy map: when are you best for deep work vs admin?",
    "- Feedback style you prefer when you’re wrong?",
    "- Three coping strategies that reliably help under stress.",
]


def _normalize_created_at(value: Any) -> str:
    return normalization.normalize_created_at(value)


def _invoke_structured_llm(
    prompt_path: str,
    variables: dict[str, str],
    *,
    response_model: type[Any],
    agent_name: str,
    config: AppConfig,
    timeout: float | None = None,
    max_attempts: int = 2,
    retry_message: str | None = None,
) -> Any:
    """Proxy to summarize command helper while honoring patched runners."""

    original_runner = summarize_commands.run_ollama_agent
    original_builder = summarize_commands.build_ollama_config_from_mapping
    summarize_commands.run_ollama_agent = run_ollama_agent
    summarize_commands.build_ollama_config_from_mapping = build_ollama_config_from_mapping
    try:
        return _commands_invoke_structured_llm(
            prompt_path,
            variables,
            response_model=response_model,
            agent_name=agent_name,
            config=config,
            timeout=timeout,
            max_attempts=max_attempts,
            retry_message=retry_message,
        )
    finally:
        summarize_commands.build_ollama_config_from_mapping = original_builder
        summarize_commands.run_ollama_agent = original_runner


def _structured_call_with_retry(
    func: Any,
    *,
    retries: int,
    label: str,
) -> Any:
    return func()


def _summarize_day_payload(
    entries: Sequence[NormalizedEntry],
    date: str,
    config: AppConfig,
    *,
    timeout: float | None,
    retries: int,
) -> Any:
    """Proxy to the summarize command helper with test-friendly overrides."""

    return summarize_commands._summarize_day_payload(
        entries,
        date,
        config,
        timeout=timeout,
        retries=retries,
        invoke_structured_llm=_invoke_structured_llm,
        structured_call=_structured_call_with_retry,
        use_fake_llm_override=use_fake_llm(),
    )


def _latest_pending_batch(workspace: Path, config: AppConfig) -> Path | None:
    directory = _pending_updates_dir(workspace, config)
    if not directory.exists():
        return None
    files = sorted(p for p in directory.glob("*.yaml") if p.is_file())
    return files[-1] if files else None


@app.command()
def init(
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Directory to initialize (defaults to current working directory).",
    ),
) -> None:
    """Initialize the local aijournal layout."""
    summary = run_init(path)
    typer.echo(summary)


@ops_dev_app.command("new", hidden=True)
def new(
    title: str | None = typer.Argument(
        None,
        help="Title for the journal entry; omit when using --fake.",
    ),
    tags: list[str] | None = typer.Option(
        None,
        "--tags",
        "-t",
        help="Tag to attach to the entry (repeatable).",
    ),
    fake: int = typer.Option(
        0,
        "--fake",
        min=0,
        help="Generate N fake entries with deterministic metadata (no LLM).",
    ),
    seed: int | None = typer.Option(
        None,
        "--seed",
        help="Optional RNG seed for --fake generation.",
    ),
) -> None:
    """Create a new journal entry or synthesize fake entries for testing."""
    _emit_deprecation("aijournal ops dev new", "aijournal capture --text")
    run_new(title, tags, fake, seed, _get_workspace())


@ops_dev_app.command("human-sim", hidden=True)
def dev_human_sim(
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Optional workspace path to populate (defaults to a temp directory).",
    ),
    keep_workspace: bool = typer.Option(
        False,
        "--keep-workspace/--cleanup-workspace",
        help="Preserve the generated workspace after the run (auto-enabled when --output is set).",
    ),
    max_stage: int = typer.Option(
        8,
        "--max-stage",
        min=0,
        max=8,
        help="Maximum pipeline stage to execute (0=persist, 8=pack).",
    ),
    pack_level: str = typer.Option(
        "L1",
        "--pack-level",
        help="Pack level to request when packs are enabled (L1, L3, or L4).",
    ),
) -> None:
    """Run the human simulator with configurable stage depth for validation."""

    from aijournal.simulator.orchestrator import HumanSimulator

    resolved_pack = pack_level.upper()
    if resolved_pack not in {"L1", "L3", "L4"}:
        typer.secho("--pack-level must be one of L1, L3, or L4", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    pack_literal = cast(Literal["L1", "L3", "L4"], resolved_pack)
    simulator = HumanSimulator(max_stage=max_stage, pack_level=pack_literal)
    report = simulator.run(
        workspace=output,
        keep_workspace=keep_workspace or output is not None,
    )
    typer.echo(report.render())
    typer.echo("Result: " + ("PASS" if report.validation.ok else "FAIL"))
    if not report.validation.ok:
        raise typer.Exit(1)


@ops_pipeline_app.command("ingest", hidden=True)
def ingest(
    sources: list[Path] = typer.Argument(
        ...,
        exists=True,
        dir_okay=True,
        file_okay=True,
        readable=True,
        resolve_path=True,
        help="Markdown files or directories to ingest.",
    ),
    source_type: str = typer.Option(
        "external",
        "--source-type",
        help="Label recorded in the manifest for these sources.",
        rich_help_panel="METADATA",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Maximum number of files to ingest.",
        rich_help_panel="CONTROL",
    ),
    snapshot: bool = typer.Option(
        True,
        "--snapshot/--no-snapshot",
        help="Store raw copies under data/raw/<hash>.md.",
        rich_help_panel="IMPORT",
    ),
) -> None:
    """Ingest Markdown posts into normalized YAML via Ollama."""
    _emit_deprecation("aijournal ops pipeline ingest", "aijournal capture --from")
    run_ingest(
        sources,
        _get_workspace(),
        source_type=source_type,
        limit=limit,
        snapshot=snapshot,
    )


@ops_pipeline_app.command("normalize")
def normalize(
    entry: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Path to journal Markdown entry.",
    ),
) -> None:
    """Normalize a Markdown journal entry into structured YAML."""
    entry = entry.resolve()
    try:
        frontmatter, sections = _parse_entry(entry)
    except ValueError as err:
        typer.secho(str(err), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    entry_id_value = frontmatter.get("id")
    created_value = frontmatter.get("created_at")
    title_value = frontmatter.get("title")
    tags = frontmatter.get("tags", []) or []

    if not all([entry_id_value, created_value, title_value]):
        typer.secho(
            "Frontmatter must include id, created_at, title.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    entry_id = str(entry_id_value)
    title = str(title_value)
    created_str = _normalize_created_at(created_value)
    date_str = time_utils.created_date(created_str)
    workspace = find_data_root(entry)
    config = load_config(workspace)
    normalized_data = {
        "id": entry_id,
        "created_at": created_str,
        "source_path": _relative_source_path(entry, workspace),
        "title": title,
        "tags": tags,
        "sections": sections,
    }

    output_path = normalized_entry_path(workspace, date_str, entry_id, paths=config.paths)
    _write_yaml_if_changed(
        output_path,
        normalized_data,
        schema="normalized_entry",
    )
    typer.echo(str(output_path))


@ops_pipeline_app.command("summarize", hidden=True)
def summarize(
    date: str = typer.Option(
        ...,
        "--date",
        "-d",
        help="Date (YYYY-MM-DD) to summarize.",
        rich_help_panel="INPUT",
    ),
    timeout: float = typer.Option(
        DEFAULT_TIMEOUT_SECONDS,
        "--timeout",
        help="Seconds to wait for the LLM response before retrying.",
        show_default=True,
        rich_help_panel="LLM",
    ),
    retries: int = typer.Option(
        DEFAULT_LLM_RETRIES,
        "--retries",
        min=0,
        help="Number of retry attempts when the model times out or returns invalid JSON.",
        show_default=True,
        rich_help_panel="LLM",
    ),
    progress: bool = typer.Option(
        False,
        "--progress/--no-progress",
        help="Print progress for each normalized entry before calling the model.",
        rich_help_panel="LLM",
    ),
) -> None:
    """Generate a daily summary from normalized entries."""
    _emit_deprecation("aijournal ops pipeline summarize", "aijournal capture --from/--text")
    ctx = _run_context("summarize")
    summary_path = run_summarize_command(
        ctx,
        DailySummaryOptions(
            date=date,
            timeout=timeout,
            retries=retries,
            progress=progress,
        ),
    )
    typer.echo(str(summary_path))


@ops_pipeline_app.command("extract-facts", hidden=True)
def facts(
    date: str = typer.Option(
        ...,
        "--date",
        "-d",
        help="Date (YYYY-MM-DD) to analyze.",
        rich_help_panel="INPUT",
    ),
    timeout: float = typer.Option(
        DEFAULT_TIMEOUT_SECONDS,
        "--timeout",
        help="Seconds to wait for the LLM response before retrying.",
        show_default=True,
        rich_help_panel="LLM",
    ),
    retries: int = typer.Option(
        DEFAULT_LLM_RETRIES,
        "--retries",
        min=0,
        help="Number of retry attempts when the model times out or returns invalid JSON.",
        show_default=True,
        rich_help_panel="LLM",
    ),
    progress: bool = typer.Option(
        False,
        "--progress/--no-progress",
        help="Print progress for each normalized entry before calling the model.",
        rich_help_panel="LLM",
    ),
) -> None:
    """Generate micro-facts from normalized entries."""
    _emit_deprecation("aijournal ops pipeline extract-facts", "aijournal capture --from/--text")
    workspace = _get_workspace()
    config = load_config(workspace)
    _, claim_models = load_profile_components(workspace, config=config)
    ctx = _run_context("facts", workspace=workspace, config=config)
    output = run_facts_command(
        ctx,
        FactsOptions(
            date=date,
            timeout=timeout,
            retries=retries,
            progress=progress,
            claim_models=claim_models,
            preview_builder=lambda proposals, claims, timestamp: _build_claim_preview(
                proposals,
                claims,
                timestamp=timestamp,
            ),
        ),
    )
    preview, facts_path = output.preview, output.path
    if preview:
        _print_claim_preview(preview)
    typer.echo(str(facts_path))


@profile_app.command("suggest")
def profile_suggest(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to analyze."),
    timeout: float = typer.Option(
        DEFAULT_TIMEOUT_SECONDS,
        "--timeout",
        help="Seconds to wait for the LLM response before retrying.",
        show_default=True,
    ),
    retries: int = typer.Option(
        DEFAULT_LLM_RETRIES,
        "--retries",
        min=0,
        help="Number of retry attempts when the model times out or returns invalid JSON.",
        show_default=True,
    ),
    progress: bool = typer.Option(
        False,
        "--progress/--no-progress",
        help="Print progress for each normalized entry before calling the model.",
    ),
) -> None:
    """Suggest profile updates based on normalized entries."""
    path = run_profile_suggest(
        date,
        timeout=timeout,
        retries=retries,
        progress=progress,
    )
    typer.echo(str(path))


@profile_app.command("apply")
def profile_apply(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to apply."),
    file: Path | None = typer.Option(None, "--file", help="Path to suggestions YAML."),
    yes: bool = typer.Option(False, "--yes", help="Apply without prompting."),
) -> None:
    """Apply profile suggestions to authoritative files (offline)."""
    message = run_profile_apply(
        date,
        suggestions_path=file,
        auto_confirm=yes,
    )
    typer.echo(message)


@ops_pipeline_app.command("characterize", hidden=True)
def characterize(
    date: str = typer.Option(
        ...,
        "--date",
        "-d",
        help="Date (YYYY-MM-DD) to analyze.",
        rich_help_panel="INPUT",
    ),
    timeout: float = typer.Option(
        DEFAULT_TIMEOUT_SECONDS,
        "--timeout",
        help="Seconds to wait for the LLM response before retrying.",
        show_default=True,
        rich_help_panel="LLM",
    ),
    retries: int = typer.Option(
        DEFAULT_LLM_RETRIES,
        "--retries",
        min=0,
        help="Number of retry attempts when the model times out or returns invalid JSON.",
        show_default=True,
        rich_help_panel="LLM",
    ),
    progress: bool = typer.Option(
        False,
        "--progress/--no-progress",
        help="Print progress for each normalized entry before calling the model.",
        rich_help_panel="LLM",
    ),
) -> None:
    """Derive pending profile updates from normalized entries."""
    _emit_deprecation("aijournal ops pipeline characterize", "aijournal capture --from/--text")
    batch_path = run_characterize(
        date,
        timeout=timeout,
        retries=retries,
        progress=progress,
        build_claim_preview=lambda proposals, claims, ts: _build_claim_preview(
            proposals,
            claims,
            timestamp=ts,
        ),
        normalize_claims=_normalize_claim_proposals,
        invoke_structured_llm=_invoke_structured_llm,
        structured_call=_structured_call_with_retry,
    )
    typer.echo(str(batch_path))


@ops_pipeline_app.command("review", hidden=True)
def review_updates(
    file: Path | None = typer.Option(
        None,
        "--file",
        help="Specific pending batch to review (defaults to latest).",
        rich_help_panel="INPUT",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the proposed updates.",
        rich_help_panel="ACTIONS",
    ),
) -> None:
    """Review or apply pending profile update batches."""
    _emit_deprecation("aijournal ops pipeline review", "aijournal capture --apply-profile review")
    workspace = _get_workspace()
    config = load_config(workspace)
    batch_path = file or _latest_pending_batch(workspace, config)
    if batch_path is None:
        typer.secho("No pending profile update batches found.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if not batch_path.exists():
        typer.secho(f"Batch file not found: {batch_path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    batch = load_artifact_data(batch_path, ProfileUpdateBatch)
    claim_proposals: list[ClaimProposal] = [
        proposal.model_copy(deep=True) for proposal in batch.proposals.claims
    ]
    facet_proposals: list[FacetChange] = [
        proposal.model_copy(deep=True) for proposal in batch.proposals.facets
    ]

    batch_id = batch.batch_id or batch_path.stem
    typer.echo(
        f"Batch {batch_id}: {len(claim_proposals)} claim(s), {len(facet_proposals)} facet(s)",
    )

    for claim_proposal in claim_proposals:
        label = (
            claim_proposal.normalized_ids[0]
            if claim_proposal.normalized_ids
            else claim_proposal.statement[:48]
        )
        typer.echo(f"- claim {label}: {claim_proposal.statement}")

    for facet_proposal in facet_proposals:
        if facet_proposal.path:
            typer.echo(f"- facet {facet_proposal.path}: {facet_proposal.value}")

    if not apply:
        if batch.preview and batch.preview.claim_events:
            _print_claim_preview(batch.preview)
        else:
            _preview_claim_consolidation(workspace, claim_proposals, config=config)
        if batch.preview and batch.preview.interview_prompts:
            typer.echo("Hint: run `aijournal interview` to follow up on the queued prompts.")
        return

    profile_model, claim_models = load_profile_components(workspace, config=config)
    profile = profile_to_dict(profile_model)
    claims_data = [claim.model_copy(deep=True) for claim in claim_models]
    timestamp = time_utils.format_timestamp(time_utils.now())
    applied = 0
    merge_events: list[ClaimMergeOutcome] = []

    for claim_proposal in claim_proposals:
        incoming_atom = _claim_proposal_to_atom(claim_proposal, timestamp=timestamp)
        if apply_claim_upsert(claims_data, incoming_atom, timestamp, events=merge_events):
            applied += 1

    for facet_proposal in facet_proposals:
        if not facet_proposal.path:
            continue
        if apply_profile_update(profile, facet_proposal.path, facet_proposal.value, timestamp):
            applied += 1

    if not applied:
        typer.echo("No changes applied")
        return

    updated_profile = SelfProfile.model_validate(profile)
    updated_claims = [claim.model_copy(deep=True) for claim in claims_data]
    profile_dir = Path(config.paths.profile)
    if not profile_dir.is_absolute():
        profile_dir = workspace / profile_dir
    write_yaml_model(profile_dir / "self_profile.yaml", updated_profile)
    write_yaml_model(profile_dir / "claims.yaml", ClaimsFile(claims=updated_claims))
    _emit_claim_merge_events(merge_events, "Applied claim consolidations:")
    typer.echo(f"Applied {applied} updates from {batch_path}")


@app.command()
def advise(
    question: str = typer.Argument(..., help="Question for the advisor to answer."),
) -> None:
    """Generate advice from the current profile."""
    ctx = _run_context("advise")
    advice_path = run_advise_command(ctx, AdviceOptions(question=question))
    typer.echo(str(advice_path))


@ollama_app.command("health")
def ollama_health() -> None:
    """Inspect Ollama availability for both fake and live modes."""
    if use_fake_llm():
        models = [
            {"name": "llama3.1:8b-instruct", "size": "8B", "quant": "Q4_K_M"},
            {"name": "llama3.1:70b-instruct", "size": "70B", "quant": "Q4_K_M"},
        ]
        payload = {
            "endpoint": "fake://ollama",
            "default": models[0]["name"],
            "models": models,
        }
        typer.echo(dump_yaml(payload, sort_keys=False).rstrip())
        return

    host = os.getenv("AIJOURNAL_OLLAMA_HOST")
    base = resolve_ollama_host(host)
    try:
        response = httpx.get(f"{base}/api/tags", timeout=15.0)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:  # pragma: no cover - depends on runtime host
        typer.secho(f"Unable to query Ollama: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    models_raw = data.get("models") if isinstance(data, dict) else None
    models_payload: list[dict[str, Any]] = []
    if isinstance(models_raw, list):
        for item in models_raw:
            item_data = item if isinstance(item, dict) else {}
            models_payload.append(
                {
                    "name": item_data.get("name") or item_data.get("model"),
                    "size": item_data.get("size"),
                    "digest": item_data.get("digest"),
                    "modified_at": item_data.get("modified_at") or item_data.get("last_modified"),
                },
            )

    workspace = _get_workspace()
    config = load_config(workspace)
    payload = {
        "endpoint": base,
        "default": build_ollama_config_from_mapping(config).model,
        "models": models_payload,
    }
    typer.echo(dump_yaml(payload, sort_keys=False).rstrip())


@persona_app.command("build")
def persona_build(
    token_budget: int | None = typer.Option(
        None,
        help="Override the persona_core token budget (default 1200).",
    ),
    max_claims: int | None = typer.Option(
        None,
        help="Limit the number of claims considered for persona core.",
    ),
    min_claims: int | None = typer.Option(
        None,
        help="Guarantee at least this many claims remain even if over budget.",
    ),
) -> None:
    """Regenerate derived/persona/persona_core.yaml."""
    workspace = _get_workspace()
    config = load_config(workspace)
    profile_model, claim_models = load_profile_components(workspace, config=config)
    profile = profile_to_dict(profile_model)
    path, changed = run_persona_build(
        profile,
        claim_models,
        config=config,
        root=workspace,
        token_budget_override=token_budget,
        max_claims_override=max_claims,
        min_claims_override=min_claims,
    )
    status = "Wrote" if changed else "Persona core already up to date"
    typer.echo(f"{status}: {path}")


@persona_app.command("status")
def persona_status() -> None:
    """Check whether persona_core.yaml matches the latest profile edits."""
    workspace = _get_workspace()
    config = load_config(workspace)
    status, reasons = persona_state(workspace, workspace, config)
    if status == "fresh":
        typer.echo("Persona core is up to date (profile files unchanged).")
        return

    heading = "Persona core missing" if status == "missing" else "Persona core is stale"
    color = typer.colors.RED if status == "missing" else typer.colors.YELLOW
    typer.secho(heading, fg=color, err=True)
    for reason in reasons:
        typer.echo(f"- {reason}", err=True)
    typer.echo("Run `aijournal persona build` to refresh.")
    raise typer.Exit(1)


@persona_app.command("export")
def persona_export(
    variants: list[str] = typer.Option(
        ["short"],
        "--variant",
        "-v",
        help="Preset sizes to render (tiny, short, full, or all). Repeat for multiples.",
        show_default=False,
    ),
    tokens: int | None = typer.Option(
        None,
        "--tokens",
        help="Approximate token budget override (takes precedence over --variant).",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Destination file; defaults to stdout when omitted.",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        help="Write persona exports into this directory (one file per variant).",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Allow overwriting an existing --output file.",
    ),
    deterministic: bool = typer.Option(
        True,
        "--deterministic/--no-deterministic",
        help="Use stable ordering; disable to add seeded randomness.",
    ),
    seed: int | None = typer.Option(
        None,
        "--seed",
        help="Optional seed used for tie-breaking when deterministic is enabled.",
    ),
    sort: str = typer.Option(
        "strength",
        "--sort",
        help="Claim ordering: strength, recency, or id.",
        show_default=True,
    ),
    max_items: int | None = typer.Option(
        None,
        "--max-items",
        help="Optional hard cap on number of claims to include.",
    ),
    no_claim_markers: bool = typer.Option(
        False,
        "--no-claim-markers",
        help="Omit [claim:<id>] markers when listing claims.",
    ),
) -> None:
    """Render the current persona as Markdown for downstream LLM contexts."""

    expanded = _normalize_persona_variants(variants)
    _validate_persona_export_flags(
        expanded_variants=expanded,
        tokens=tokens,
        max_items=max_items,
        output=output,
        output_dir=output_dir,
    )

    sort_key = sort.lower().strip()
    if sort_key not in {"strength", "recency", "id"}:
        raise typer.BadParameter(
            "--sort must be one of: strength, recency, id.",
            param_hint="--sort",
        )

    workspace = _get_workspace()
    config = load_config(workspace)
    try:
        persona = load_persona_core(workspace, config)
    except PersonaArtifactMissingError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    rendered: list[RenderedPersona] = []
    for variant_value in expanded:
        options = PersonaExportOptions(
            variant=variant_value,
            token_budget=tokens,
            sort_by=sort_key,  # type: ignore[arg-type]
            deterministic=deterministic,
            seed=seed,
            max_items=max_items,
            include_claim_markers=not no_claim_markers,
        )

        try:
            result = export_persona_markdown(persona, config=config, options=options)
        except PersonaContentError as exc:
            typer.secho(str(exc), fg=typer.colors.YELLOW, err=True)
            raise typer.Exit(1) from exc
        label = _persona_variant_label(variant_value, tokens)
        rendered.append((label, variant_value, result))

    if _write_persona_exports(rendered, workspace, output, output_dir, overwrite):
        return

    if len(rendered) == 1:
        typer.echo(rendered[0][2].text.rstrip())
        return

    for idx, (label, _, result) in enumerate(rendered):
        typer.echo(f"<!-- persona:{label} (≈{result.approx_tokens} tokens) -->")
        typer.echo(result.text.rstrip())
        if idx < len(rendered) - 1:
            typer.echo("")
            typer.echo("---")
            typer.echo("")


def _persona_variant_label(variant: PersonaVariant, tokens: int | None) -> str:
    base = variant.value
    if tokens is None:
        return base
    return f"{base}-{tokens}"


RenderedPersona = tuple[str, PersonaVariant, PersonaExportResult]


def _normalize_persona_variants(raw_variants: Iterable[str]) -> list[PersonaVariant]:
    allowed = {member.value: member for member in PersonaVariant}
    normalized = [value.lower().strip() for value in raw_variants if value]
    if not normalized:
        normalized = [PersonaVariant.SHORT.value]

    expanded: list[PersonaVariant] = []
    seen: set[PersonaVariant] = set()
    for value in normalized:
        candidates: Iterable[PersonaVariant]
        if value == "all":
            candidates = PersonaVariant
        else:
            member = allowed.get(value)
            if member is None:
                raise typer.BadParameter(
                    "--variant must be one of: tiny, short, full, or all.",
                    param_hint="--variant",
                )
            candidates = (member,)

        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            expanded.append(candidate)

    if not expanded:
        expanded = [PersonaVariant.SHORT]
    return expanded


def _validate_persona_export_flags(
    *,
    expanded_variants: Sequence[PersonaVariant],
    tokens: int | None,
    max_items: int | None,
    output: Path | None,
    output_dir: Path | None,
) -> None:
    if tokens is not None:
        if tokens <= 0:
            raise typer.BadParameter("--tokens must be a positive integer.", param_hint="--tokens")
        if len(expanded_variants) > 1:
            raise typer.BadParameter(
                "--tokens can only be combined with a single --variant.",
                param_hint="--tokens",
            )

    if max_items is not None and max_items <= 0:
        raise typer.BadParameter(
            "--max-items must be positive when provided.",
            param_hint="--max-items",
        )

    if output is not None and output_dir is not None:
        raise typer.BadParameter(
            "Use either --output or --output-dir, not both.",
            param_hint="--output",
        )
    if output is not None and len(expanded_variants) > 1:
        raise typer.BadParameter(
            "--output only supports a single variant; use --output-dir for multiples.",
            param_hint="--output",
        )


def _write_persona_exports(
    rendered: Sequence[RenderedPersona],
    workspace: Path,
    output: Path | None,
    output_dir: Path | None,
    overwrite: bool,
) -> bool:
    if output_dir is not None:
        destination_dir = output_dir if output_dir.is_absolute() else workspace / output_dir
        destination_dir.mkdir(parents=True, exist_ok=True)
        targets: list[tuple[Path, PersonaExportResult]] = []
        for label, _, result in rendered:
            filename = f"persona-{label}.md"
            targets.append((destination_dir / filename, result))

        if not overwrite:
            for path, _ in targets:
                if path.exists():
                    typer.secho(
                        f"Refusing to overwrite existing file: {path}. Use --overwrite to replace it.",
                        fg=typer.colors.RED,
                        err=True,
                    )
                    raise typer.Exit(1)

        written: list[Path] = []
        for path, result in targets:
            path.write_text(result.text, encoding="utf-8")
            written.append(path)
        joined = ", ".join(str(path) for path in written)
        typer.echo(f"Wrote persona exports to {joined}")
        return True

    if output is not None:
        destination = output if output.is_absolute() else workspace / output
        if destination.exists() and not overwrite:
            typer.secho(
                f"Refusing to overwrite existing file: {destination}. Use --overwrite to replace it.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered[0][2].text, encoding="utf-8")
        typer.echo(f"Wrote persona export to {destination}")
        return True

    return False


def _build_targeted_probes(
    targets: Sequence[InterviewTarget],
    entries: Sequence[NormalizedEntry],
    *,
    max_items: int = 4,
) -> InterviewSet:
    title = "recent notes"
    if entries:
        first = entries[0]
        title = first.title or first.id or title

    questions: list[InterviewQuestion] = []
    for idx, target in enumerate(targets, start=1):
        if len(questions) >= max_items:
            break
        if target.kind == "pending" and target.reasons:
            prompt_text = target.reasons[0]
            questions.append(
                InterviewQuestion(
                    id=f"pending-{idx}",
                    text=prompt_text,
                    target_facet=target.path,
                    priority="high",
                ),
            )
            continue

        if target.kind == "claim":
            label = target.claim_id or target.path
            if target.missing_context:
                context_label = target.missing_context[0]
                text = f"How does {label} hold when context includes '{context_label}'?"
            else:
                text = f"What new evidence from {title} should update {label}?"
        else:
            text = f"What fresh detail from {title} should refine {target.path}?"

        if target.reasons:
            text += f" ({'; '.join(target.reasons[:2])})"

        questions.append(
            InterviewQuestion(
                id=f"ranked-{idx}",
                text=text,
                target_facet=target.path,
                priority="high" if target.score >= 1.5 else "medium",
            ),
        )
        if len(questions) >= max_items:
            break

    if len(questions) < 2:
        return InterviewSet()
    return InterviewSet(questions=questions)


def _signature_payload_from_claim(claim: ClaimAtom) -> ClaimSignaturePayload:
    scope = claim.scope or Scope()
    return ClaimSignaturePayload(
        claim_type=str(claim.type or "preference"),
        subject=str(claim.subject or ""),
        predicate=str(claim.predicate or ""),
        domain=scope.domain,
        context=[item for item in scope.context if item],
        conditions=[item for item in scope.conditions if item],
    )


def _signature_payload_from_signature(signature: ClaimSignature) -> ClaimSignaturePayload:
    domain, context, conditions = signature.scope
    return ClaimSignaturePayload(
        claim_type=signature.claim_type,
        subject=signature.subject,
        predicate=signature.predicate,
        domain=domain,
        context=[item for item in context if item],
        conditions=[item for item in conditions if item],
    )


def _conflict_payload_from_outcome(conflict: ClaimConflict) -> ClaimConflictPayload:
    return ClaimConflictPayload(
        claim_id=conflict.claim_id,
        signature=_signature_payload_from_signature(conflict.signature),
        statement=conflict.statement,
        existing_value=conflict.existing_value,
        incoming_value=conflict.incoming_value,
        incoming_sources=[source.model_copy(deep=True) for source in conflict.incoming_sources],
    )


def _format_scope_label(scope: tuple[str | None, tuple[str, ...], tuple[str, ...]]) -> str:
    domain, context, conditions = scope
    parts: list[str] = []
    if domain:
        parts.append(str(domain))
    if context:
        parts.append("/".join(context))
    if conditions:
        parts.append("|".join(conditions))
    return " :: ".join(parts) if parts else "global"


def _emit_claim_merge_events(events: list[ClaimMergeOutcome], heading: str) -> None:
    relevant = [event for event in events if event.changed]
    if not relevant:
        return
    typer.echo(heading)
    for event in relevant:
        if event.action == "upsert":
            typer.echo(f"  • new claim {event.claim_id}")
        elif event.action == "update":
            note = f" (Δstrength {event.delta_strength:+0.2f})" if event.delta_strength else ""
            typer.echo(f"  • updated {event.claim_id}{note}")
        elif event.action == "strength_delta":
            typer.echo(
                f"  • strength adjusted {event.claim_id} (Δ {event.delta_strength:+0.2f})",
            )
        elif event.action == "conflict" and event.conflict:
            conflict = event.conflict
            scope_label = _format_scope_label(conflict.signature.scope)
            typer.secho(
                (
                    f"  • conflict {event.claim_id} [{scope_label}]: "
                    f"'{conflict.existing_value}' vs '{conflict.incoming_value}'"
                ),
                fg=typer.colors.YELLOW,
            )
            if event.related_claim_id and event.related_signature:
                related_scope = _format_scope_label(event.related_signature.scope)
                action_note = f" ({event.related_action})" if event.related_action else ""
                typer.echo(
                    f"    ↳ spawned {event.related_claim_id} [{related_scope}]{action_note}",
                )
        elif event.action == "delete":
            typer.echo(f"  • deleted {event.claim_id}")


def _preview_claim_consolidation(
    workspace: Path,
    claim_proposals: Sequence[Any],
    *,
    config: AppConfig,
) -> None:
    if not claim_proposals:
        return
    _, claim_models = load_profile_components(workspace, config=config)
    if not claim_models:
        return
    timestamp = time_utils.format_timestamp(time_utils.now())
    working_claims = [claim.model_copy(deep=True) for claim in claim_models]
    consolidator = ClaimConsolidator(timestamp=timestamp)
    events: list[ClaimMergeOutcome] = []
    for proposal in claim_proposals:
        if isinstance(proposal, ClaimProposal):
            incoming = _claim_proposal_to_atom(proposal, timestamp=timestamp)
        elif isinstance(proposal, dict):
            raw_claim = proposal.get("claim") if isinstance(proposal, dict) else None
            if raw_claim is None:
                continue
            try:
                incoming = normalization.normalize_claim_atom(raw_claim, timestamp=timestamp)
            except (ValidationError, ValueError):
                continue
        else:
            continue
        outcome = consolidator.upsert(working_claims, incoming)
        if outcome.changed:
            events.append(outcome)
    _emit_claim_merge_events(events, "Preview (claim consolidation):")


def _claim_proposal_to_atom(proposal: ClaimProposal, *, timestamp: str) -> ClaimAtom:
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


def _build_claim_preview(
    claim_proposals: Sequence[ClaimProposal],
    existing_claims: Sequence[ClaimAtom],
    *,
    timestamp: str,
) -> ProfileUpdatePreview | None:
    if not claim_proposals:
        return None

    working_claims = [claim.model_copy(deep=True) for claim in existing_claims]
    consolidator = ClaimConsolidator(timestamp=timestamp)
    events: list[ClaimPreviewEvent] = []
    prompts: list[str] = []

    for proposal in claim_proposals:
        incoming = _claim_proposal_to_atom(proposal, timestamp=timestamp)
        outcome = consolidator.upsert(working_claims, incoming)
        if not outcome.changed:
            continue
        signature_payload = (
            _signature_payload_from_signature(outcome.signature)
            if outcome.signature
            else _signature_payload_from_claim(incoming)
        )
        related_signature_payload = (
            _signature_payload_from_signature(outcome.related_signature)
            if outcome.related_signature
            else None
        )
        conflict_payload = None
        if outcome.conflict:
            conflict_payload = _conflict_payload_from_outcome(outcome.conflict)
            scope_label = _format_scope_label(outcome.conflict.signature.scope)
            prompts.append(
                f"Clarify claim {outcome.claim_id} [{scope_label}]: "
                f"existing='{outcome.conflict.existing_value}' vs incoming='{outcome.conflict.incoming_value}'."
            )
        events.append(
            ClaimPreviewEvent(
                action=ClaimEventAction(outcome.action),
                claim_id=outcome.claim_id,
                delta_strength=float(outcome.delta_strength or 0.0),
                statement=incoming.statement,
                value=incoming.value,
                strength=float(incoming.strength or 0.0),
                signature=signature_payload,
                conflict=conflict_payload,
                related_claim_id=outcome.related_claim_id,
                related_action=outcome.related_action,
                related_signature=related_signature_payload,
            )
        )

    if not events and not prompts:
        return None
    return ProfileUpdatePreview(claim_events=events, interview_prompts=prompts)


def _scope_tuple_from_payload(
    signature: ClaimSignaturePayload | None,
) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    if signature is None:
        return (None, tuple(), tuple())
    return (
        signature.domain,
        tuple(signature.context),
        tuple(signature.conditions),
    )


def _print_claim_preview(preview: ProfileUpdatePreview) -> None:
    events = list(preview.claim_events)
    if events:
        typer.echo("Preview (claim consolidation):")
        for event in events:
            scope_label = _format_scope_label(_scope_tuple_from_payload(event.signature))
            if event.action == "upsert":
                typer.echo(f"  • new claim {event.claim_id} [{scope_label}]")
            elif event.action == "update":
                note = f" (Δstrength {event.delta_strength:+0.2f})" if event.delta_strength else ""
                typer.echo(f"  • updated {event.claim_id} [{scope_label}]{note}")
            elif event.action == "strength_delta":
                typer.echo(
                    (
                        f"  • strength adjusted {event.claim_id} [{scope_label}] "
                        f"(Δ {event.delta_strength:+0.2f})"
                    ),
                )
            elif event.action == "conflict" and event.conflict:
                conflict = event.conflict
                conflict_scope = _format_scope_label(
                    (
                        conflict.signature.domain,
                        tuple(conflict.signature.context),
                        tuple(conflict.signature.conditions),
                    ),
                )
                typer.secho(
                    (
                        f"  • conflict {event.claim_id} [{conflict_scope}]: "
                        f"'{conflict.existing_value}' vs '{conflict.incoming_value}'"
                    ),
                    fg=typer.colors.YELLOW,
                )
                if event.related_claim_id and event.related_signature:
                    new_scope_label = _format_scope_label(
                        _scope_tuple_from_payload(event.related_signature),
                    )
                    action_note = f" ({event.related_action})" if event.related_action else ""
                    typer.echo(
                        (
                            f"    ↳ spawned {event.related_claim_id} [{new_scope_label}]"
                            f"{action_note}"
                        ),
                    )
            elif event.action == "delete":
                typer.echo(f"  • deleted {event.claim_id} [{scope_label}]")
            else:
                typer.echo(f"  • {event.action} {event.claim_id} [{scope_label}]")

    if preview.interview_prompts:
        typer.echo("Follow-up interviews queued:")
        for prompt in preview.interview_prompts:
            typer.echo(f"  • {prompt}")


@profile_app.command("status")
def profile_status() -> None:
    """Show ranked facets/claims needing review."""
    run_profile_status()


@profile_app.command("interview")
def interview(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to review."),
) -> None:
    """Surface targeted interview probes based on stale facets."""
    workspace = _get_workspace()

    config = load_config(workspace)

    profile_model, claim_models = load_profile_components(workspace, config=config)
    profile = profile_to_dict(profile_model)
    claims = [claim.model_copy(deep=True) for claim in claim_models]
    if not profile and not claims:
        typer.secho("No profile data", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    entries = _load_normalized_entries(workspace, config, date)
    if not entries:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    weights = config.impact_weights.model_dump(mode="python")

    max_questions = _coaching_max_questions(profile)
    pending_prompts = _collect_pending_interview_prompts(workspace, config)
    rankings = _compute_rankings(
        profile,
        claims,
        weights,
        time_utils.now(),
        entries=entries,
        pending_prompts=pending_prompts,
    )

    if max_questions == 0:
        typer.echo("Interview probes:")
        typer.echo("- Coaching preferences disable probing right now.")
        return

    if use_fake_llm():
        interview_set = _build_targeted_probes(rankings, entries, max_items=max_questions)
    else:
        rankings_payload = [
            {
                "path": target.path,
                "score": target.score,
                "kind": target.kind,
                "reasons": list(target.reasons),
                "claim_id": target.claim_id,
                "missing_context": list(target.missing_context),
            }
            for target in rankings[: max(max_questions * 2, 6)]
        ]
        try:
            interview_set = cast(
                InterviewSet,
                _structured_call_with_retry(
                    lambda: _invoke_structured_llm(
                        "prompts/interview.md",
                        {
                            "date": date,
                            "profile_json": _json_block(profile),
                            "claims_json": _json_block(
                                {"claims": [claim.model_dump(mode="python") for claim in claims]}
                            ),
                            "entries_json": _json_block(_entries_to_payload(entries)),
                            "rankings_json": _json_block(rankings_payload),
                            "coaching_prefs_json": _json_block(profile.get("coaching_prefs", {})),
                        },
                        response_model=InterviewSet,
                        agent_name="aijournal-interview",
                        config=config,
                    ),
                    retries=0,
                    label="interview",
                ),
            )
        except LLMResponseError as exc:
            typer.secho(
                f"Interview generation failed ({exc}); falling back to heuristic probes.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            interview_set = InterviewSet()
        if interview_set.questions:
            interview_set.questions = interview_set.questions[:max_questions]

    if not interview_set.questions:
        interview_set = _build_targeted_probes(rankings, entries, max_items=max_questions)

    if not interview_set.questions:
        fallback_questions = [
            InterviewQuestion(
                id=f"default-{idx + 1}",
                text=probe.lstrip("- ").strip(),
                priority="baseline",
            )
            for idx, probe in enumerate(HIGH_IMPACT_PROBES)
        ]
        interview_set = InterviewSet(questions=fallback_questions[:max_questions])

    typer.echo("Interview probes:")
    for question in interview_set.questions:
        typer.echo(f"- {question.text}")


@export_app.command("pack", hidden=True)
def pack(
    level: str = typer.Option(
        "L2",
        "--level",
        "-l",
        help="Context depth (L1 or L2).",
        rich_help_panel="PACK CONFIG",
    ),
    date: str | None = typer.Option(
        None,
        "--date",
        "-d",
        help="Date (YYYY-MM-DD); auto-detected for L2 when omitted.",
        rich_help_panel="PACK CONFIG",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Destination file (defaults to stdout).",
        rich_help_panel="OUTPUT",
    ),
    max_tokens: int | None = typer.Option(
        None,
        "--max-tokens",
        help="Optional token budget when trimming persona context.",
        rich_help_panel="OUTPUT",
    ),
    fmt: str = typer.Option(
        "yaml",
        "--format",
        help="Output format: yaml or json.",
        rich_help_panel="OUTPUT",
    ),
    history_days: int = typer.Option(
        0,
        "--history-days",
        help="Number of previous days to include (L4 packs only).",
        rich_help_panel="OUTPUT",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show plan without emitting payload.",
        rich_help_panel="OUTPUT",
    ),
) -> None:
    """Assemble a context bundle for prompting."""
    _emit_deprecation("aijournal export pack", "aijournal capture --pack")
    run_pack(
        level,
        date,
        output=output,
        max_tokens=max_tokens,
        fmt=fmt,
        history_days=history_days,
        dry_run=dry_run,
    )


@index_app.command("rebuild")
def index_rebuild(
    since: str | None = typer.Option(
        None,
        "--since",
        help="Earliest date (YYYY-MM-DD or Nd) to include when rebuilding.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Maximum number of normalized files to index (debug/testing).",
    ),
) -> None:
    """Rebuild the Annoy+SQLite retrieval index from normalized YAML."""
    message = run_index_rebuild(since, limit=limit)
    typer.echo(message)


@index_app.command("update")
def index_update(
    since: str | None = typer.Option(
        None,
        "--since",
        help="Earliest date (YYYY-MM-DD or Nd) to scan for new normalized files.",
    ),
    days: int = typer.Option(
        7,
        "--days",
        help="Rolling window (days) when --since is omitted.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Maximum number of normalized files to inspect.",
    ),
) -> None:
    """Incrementally ingest new normalized entries into the retrieval index."""
    message = run_index_tail(since, days=days, limit=limit)
    typer.echo(message)


@index_app.command("search")
def index_search(
    query: str = typer.Argument(..., help="Query text to search within indexed chunks."),
    top: int = typer.Option(
        8,
        "--top",
        "-k",
        help="Number of results to display.",
    ),
    tags: str | None = typer.Option(
        None,
        "--tags",
        help="Comma- or space-separated tags to filter by (match any).",
    ),
    source: str | None = typer.Option(
        None,
        "--source",
        help="Comma- or space-separated source types to filter by.",
    ),
    date_from: str | None = typer.Option(
        None,
        "--date-from",
        help="Earliest chunk date (YYYY-MM-DD).",
    ),
    date_to: str | None = typer.Option(
        None,
        "--date-to",
        help="Latest chunk date (YYYY-MM-DD).",
    ),
) -> None:
    """Search the retrieval index and stream formatted results."""
    run_index_search(
        query,
        top=top,
        tags=tags,
        source=source,
        date_from=date_from,
        date_to=date_to,
    )


@app.command("chat")
def chat(
    question: str = typer.Argument(
        ...,
        help="Question to ask your journal assistant.",
    ),
    top: int = typer.Option(
        6,
        "--top",
        "-k",
        help="Maximum number of retrieval chunks to use.",
        rich_help_panel="RETRIEVAL FILTERS",
    ),
    tags: str | None = typer.Option(
        None,
        "--tags",
        help="Optional tag filters (comma or space separated).",
        rich_help_panel="RETRIEVAL FILTERS",
    ),
    source: str | None = typer.Option(
        None,
        "--source",
        help="Optional source-type filters (comma or space separated).",
        rich_help_panel="RETRIEVAL FILTERS",
    ),
    date_from: str | None = typer.Option(
        None,
        "--date-from",
        help="Earliest chunk date (YYYY-MM-DD).",
        rich_help_panel="RETRIEVAL FILTERS",
    ),
    date_to: str | None = typer.Option(
        None,
        "--date-to",
        help="Latest chunk date (YYYY-MM-DD).",
        rich_help_panel="RETRIEVAL FILTERS",
    ),
    session: str | None = typer.Option(
        None,
        "--session",
        help="Session identifier (defaults to chat-YYYYMMDD-HHMMSS).",
        rich_help_panel="SESSION",
    ),
    save: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Persist the turn under derived/chat_sessions/<session>.",
        rich_help_panel="SESSION",
    ),
    feedback: str | None = typer.Option(
        None,
        "--feedback",
        help="Provide 'up' or 'down' to nudge cited claim strengths.",
        rich_help_panel="SESSION",
    ),
) -> None:
    """Run a retrieval-augmented chat turn against your journal."""
    run_chat(
        question,
        _get_workspace(),
        top=top,
        tags=tags,
        source=source,
        date_from=date_from,
        date_to=date_to,
        session=session,
        save=save,
        feedback=feedback,
    )


@serve_app.command("chat", hidden=True)
def serve_chat(
    host: str = typer.Option("127.0.0.1", "--host", help="Host interface to bind."),
    port: int = typer.Option(8080, "--port", help="Port to listen on."),
) -> None:
    """Start the FastAPI chat daemon (chatd)."""
    _emit_deprecation("aijournal serve chat", "the REST capture API (POST /capture)")
    run_chatd(host, port)


@ops_feedback_app.command("apply")
def feedback_apply(
    archive: bool = typer.Option(
        True,
        "--archive/--delete",
        help="Archive processed feedback batches (default) or delete them after applying.",
    ),
) -> None:
    """Apply and clear pending chat feedback batches."""

    workspace = _get_workspace()
    pending_dir = workspace / "derived" / "pending" / "profile_updates"
    if not pending_dir.exists():
        typer.secho(
            "No pending feedback batches were found (derived/pending/profile_updates missing).",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(1)

    batch_paths = sorted(pending_dir.glob("feedback_*.yaml"))
    if not batch_paths:
        typer.secho("No feedback batches to apply.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)

    claims_path = workspace / "profile" / "claims.yaml"
    if not claims_path.exists():
        typer.secho(
            "Claims file not found at profile/claims.yaml; run `aijournal ops profile status` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    claims_file = load_yaml_model(claims_path, ClaimsFile)
    claims_by_id = {claim.id: claim for claim in claims_file.claims}
    total_adjustments: list[tuple[str, float, float]] = []

    archive_dir = pending_dir / "applied_feedback"
    if archive:
        archive_dir.mkdir(parents=True, exist_ok=True)

    for path in batch_paths:
        try:
            batch = load_artifact_data(path, FeedbackBatch)
        except Exception as exc:  # pragma: no cover - malformed artifact
            typer.secho(
                f"Skipping {path.name}: {exc}",
                fg=typer.colors.RED,
                err=True,
            )
            continue

        for event in batch.events:
            target_claim = claims_by_id.get(event.claim_id)
            if target_claim is None:
                typer.secho(
                    f"{path.name} references unknown claim '{event.claim_id}' — skipping.",
                    fg=typer.colors.YELLOW,
                    err=True,
                )
                continue

            old_value = float(target_claim.strength)
            clamped_value = max(0.0, min(1.0, float(event.new_strength)))
            target_claim.strength = clamped_value
            total_adjustments.append((event.claim_id, old_value, clamped_value))

        if archive:
            archive_path = _unique_archive_path(archive_dir / path.name)
            path.rename(archive_path)
        else:
            path.unlink()

    if not total_adjustments:
        typer.secho("No claim adjustments were applied.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)

    write_yaml_model(claims_path, ClaimsFile(claims=list(claims_by_id.values())))

    typer.echo(f"Applied {len(total_adjustments)} feedback adjustment(s):")
    for claim_id, old_value, new_value in total_adjustments:
        delta = new_value - old_value
        sign = "+" if delta >= 0 else ""
        typer.echo(f"- {claim_id}: {old_value:.2f} -> {new_value:.2f} ({sign}{delta:.2f})")


def _unique_archive_path(target: Path) -> Path:
    """Return a unique path by appending a counter when needed."""

    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _coaching_max_questions(profile: dict[str, Any]) -> int:
    prefs = profile.get("coaching_prefs") if isinstance(profile, dict) else {}
    probing = prefs.get("probing") if isinstance(prefs, dict) else None
    max_questions = coerce_int(probing.get("max_questions")) if isinstance(probing, dict) else None
    if max_questions is None or max_questions < 0:
        return 3
    return int(max_questions)


# Ensure Typer command metadata exposes stable names for tests and tooling.
for _command in app.registered_commands:
    if _command.name is None and _command.callback is not None:
        _command.name = _command.callback.__name__.replace("_", "-")

for _group_name in ("profile", "ollama", "index", "persona"):
    if not any(info.name == _group_name for info in app.registered_commands):
        app.registered_commands.append(
            CommandInfo(name=_group_name, callback=lambda _name=_group_name: None, hidden=True)
        )
