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
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, cast

import httpx
import typer
import yaml
from pydantic import ValidationError
from typer.models import CommandInfo

from aijournal.api.capture import CaptureRequest
from aijournal.commands import summarize as summarize_commands
from aijournal.commands.advise import (
    _collect_pending_interview_prompts,
    run_advise,
)
from aijournal.commands.audit import run_audit_provenance
from aijournal.commands.characterize import (
    _normalize_claim_proposals,
    _pending_updates_dir,
    run_characterize,
)
from aijournal.commands.chat import run_chat
from aijournal.commands.chatd import run_chatd
from aijournal.commands.facts import run_facts
from aijournal.commands.index import (
    run_index_rebuild,
    run_index_search,
    run_index_tail,
)
from aijournal.commands.ingest import (
    _load_config,
    _parse_entry,
    _relative_source_path,
    _use_fake_llm,
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
    _entries_to_payload,
    _json_block,
    _load_normalized_entries,
    run_summarize,
)
from aijournal.commands.summarize import (
    _invoke_structured_llm as _commands_invoke_structured_llm,
)
from aijournal.commands.summarize import (
    _structured_call_with_retry as _commands_structured_call_with_retry,
)
from aijournal.commands.system import run_status_summary, run_system_doctor
from aijournal.domain.changes import ClaimProposal, FacetChange
from aijournal.domain.events import (
    ClaimConflictPayload,
    ClaimPreviewEvent,
    ClaimSignaturePayload,
    FeedbackBatch,
)
from aijournal.domain.evidence import redact_source_text
from aijournal.domain.journal import NormalizedEntry
from aijournal.domain.persona import InterviewQuestion, InterviewSet
from aijournal.io.artifacts import load_artifact_data
from aijournal.io.yaml_io import load_yaml_model, write_yaml_model
from aijournal.models.authoritative import ClaimsFile, SelfProfile
from aijournal.models.claim_atoms import ClaimAtom, ClaimSource, Scope
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
from aijournal.utils import time as time_utils
from aijournal.utils.coercion import coerce_int
from aijournal.utils.paths import (
    find_data_root,
    normalized_entry_path,
)

app = typer.Typer(help="Local-first personal journal utilities.")
profile_app = typer.Typer(help="Profile utilities.")
ollama_app = typer.Typer(help="Ollama helpers.")
index_app = typer.Typer(help="Retrieval index utilities.")
persona_app = typer.Typer(help="Persona utilities.")

# Phase 1 scaffold: advanced operations namespace and placeholder groups.
ops_app = typer.Typer(help="Advanced operations namespace.")
ops_pipeline_app = typer.Typer(help="Pipeline tools (normalize, summarize, characterize).")
ops_feedback_app = typer.Typer(help="Feedback processing utilities.")
ops_system_app = typer.Typer(help="System diagnostics and doctor helpers.")
ops_dev_app = typer.Typer(help="Developer fixtures and helpers.")
ops_audit_app = typer.Typer(help="Audit and governance utilities.")

ops_app.add_typer(ops_pipeline_app, name="pipeline")
ops_app.add_typer(profile_app, name="profile")
ops_app.add_typer(index_app, name="index")
ops_app.add_typer(persona_app, name="persona")
ops_app.add_typer(ops_feedback_app, name="feedback")
ops_app.add_typer(ops_system_app, name="system")
ops_app.add_typer(ops_dev_app, name="dev")
ops_app.add_typer(ops_audit_app, name="audit")

ops_system_app.add_typer(ollama_app, name="ollama")

app.add_typer(ops_app, name="ops")

export_app = typer.Typer(help="Context export utilities.")
serve_app = typer.Typer(help="Service runners and daemons.")

app.add_typer(export_app, name="export")
app.add_typer(serve_app, name="serve")

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
        1,
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

    root = Path.cwd()
    summary = run_status_summary(root)

    persona = summary["persona"]
    index_info = summary["index"]
    pending = summary["pending_updates"]
    ollama = summary["ollama"]

    exit_code = 0

    typer.echo("Workspace status:\n")

    if persona["status"] == "fresh":
        typer.secho("Persona: fresh", fg=typer.colors.GREEN)
    else:
        color = typer.colors.YELLOW if persona["status"] == "stale" else typer.colors.RED
        typer.secho(f"Persona: {persona['status']}", fg=color)
        for reason in persona["reasons"]:
            typer.echo(f"  - {reason}")
        exit_code = 1

    index_messages: list[str] = []
    if index_info["has_index_db"] and index_info["has_annoy_index"]:
        typer.secho("Index: ready", fg=typer.colors.GREEN)
    else:
        typer.secho("Index: missing artifacts", fg=typer.colors.RED)
        if not index_info["has_index_db"]:
            index_messages.append("index.db not found")
        if not index_info["has_annoy_index"]:
            index_messages.append("annoy.index not found")
        exit_code = 1
    if index_info.get("meta_error"):
        index_messages.append(f"meta error: {index_info['meta_error']}")
    elif index_info.get("meta"):
        meta = index_info["meta"] or {}
        chunk_count = meta.get("chunk_count")
        entry_count = meta.get("entry_count")
        updated_at = meta.get("updated_at")
        pieces = []
        if chunk_count is not None:
            pieces.append(f"chunks={chunk_count}")
        if entry_count is not None:
            pieces.append(f"entries={entry_count}")
        if updated_at:
            pieces.append(f"updated={updated_at}")
        if pieces:
            index_messages.append(" ".join(pieces))
    for line in index_messages:
        typer.echo(f"  {line}")

    pending_count = pending["count"]
    if pending_count:
        typer.secho(
            f"Pending profile updates: {pending_count}",
            fg=typer.colors.YELLOW,
        )
        for name in pending["samples"]:
            typer.echo(f"  - {name}")
    else:
        typer.secho("Pending profile updates: none", fg=typer.colors.GREEN)

    typer.echo(f"Ollama host: {ollama['host']}")
    typer.echo("Run `aijournal ops system doctor` for detailed diagnostics.")

    if exit_code:
        raise typer.Exit(exit_code)


@ops_system_app.command("doctor")
def system_doctor() -> None:
    """Run system diagnostics and emit machine-readable results."""

    root = Path.cwd()
    result = run_system_doctor(root)

    typer.echo("System diagnostics:\n")
    for check in result["checks"]:
        ok = bool(check.get("ok"))
        color = typer.colors.GREEN if ok else typer.colors.RED
        status_text = "ok" if ok else "failed"
        typer.secho(f"{check['name']}: {status_text}", fg=color)
        hint = check.get("hint")
        if hint:
            typer.echo(f"  hint: {hint}")
        details = check.get("details")
        if isinstance(details, dict):
            for key, value in details.items():
                if value in (None, [], {}, ""):
                    continue
                if isinstance(value, (list, tuple)):
                    display = ", ".join(str(item) for item in value)
                elif isinstance(value, dict):
                    display = json.dumps(value, ensure_ascii=False)
                else:
                    display = str(value)
                typer.echo(f"  {key}: {display}")

    typer.echo("\nJSON summary:")
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))

    if not result["ok"]:
        raise typer.Exit(1)


@ops_audit_app.command("provenance")
def audit_provenance_command(
    fix: bool = typer.Option(
        False,
        "--fix/--no-fix",
        help="Redact span.text fields when present instead of only reporting them.",
    ),
) -> None:
    """Scan claims and derived artifacts for span.text remnants."""

    root = Path.cwd()
    results = run_audit_provenance(root=root, fix=fix)
    if not results:
        typer.echo("No provenance span text detected.")
        return

    if fix:
        total_spans = sum(result.count for result in results)
        for result in results:
            typer.secho(
                f"Redacted {result.count} span{'s' if result.count != 1 else ''} in {result.path.as_posix()}.",
                fg=typer.colors.GREEN,
            )
        typer.echo(
            f"Redacted {total_spans} span{'s' if total_spans != 1 else ''} across {len(results)} file{'s' if len(results) != 1 else ''}.",
        )
        return

    typer.secho("Found provenance span text in:", fg=typer.colors.YELLOW)
    for result in results:
        typer.echo(f"- {result.path.as_posix()}")
        for issue in result.issues:
            spans = ", ".join(str(idx) for idx in issue.span_indices)
            entry_details = f" entry_id={issue.entry_id}" if issue.entry_id else ""
            typer.echo(f"    {issue.path} spans={spans}{entry_details}")
    typer.secho("Run with --fix to redact these spans.", fg=typer.colors.YELLOW)
    raise typer.Exit(1)


@app.callback()
def main() -> None:
    """Aijournal command-line interface."""
    # Intentionally empty; commands provide functionality.
    return


PENDING_UPDATES_SUBDIR = "derived/pending/profile_updates"

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


DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_LLM_RETRIES = 1


def _normalize_created_at(value: Any) -> str:
    return normalization.normalize_created_at(value)


def _invoke_structured_llm(
    prompt_path: str,
    variables: dict[str, str],
    *,
    response_model: type[Any],
    agent_name: str,
    config: dict[str, Any],
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
    return _commands_structured_call_with_retry(func, retries=retries, label=label)


def _summarize_day_payload(
    entries: Sequence[NormalizedEntry],
    date: str,
    config: dict[str, Any],
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
        use_fake_llm=_use_fake_llm(),
    )


def _latest_pending_batch(root: Path) -> Path | None:
    directory = _pending_updates_dir(root)
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
    run_new(title, tags, fake, seed)


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
    root = find_data_root(entry)
    normalized_data = {
        "id": entry_id,
        "created_at": created_str,
        "source_path": _relative_source_path(entry, root),
        "title": title,
        "tags": tags,
        "sections": sections,
    }

    output_path = normalized_entry_path(root, date_str, entry_id)
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
    summary_path = run_summarize(
        date,
        timeout=timeout,
        retries=retries,
        progress=progress,
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
    root = Path.cwd()
    _, claim_models = load_profile_components(root)
    preview, facts_path = run_facts(
        date,
        timeout=timeout,
        retries=retries,
        progress=progress,
        claim_models=claim_models,
        build_claim_preview=lambda proposals, claims, timestamp: _build_claim_preview(
            proposals,
            claims,
            timestamp=timestamp,
        ),
    )
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
    root = Path.cwd()
    batch_path = file or _latest_pending_batch(root)
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
            else claim_proposal.claim.statement[:48]
        )
        typer.echo(f"- claim {label}: {claim_proposal.claim.statement}")

    for facet_proposal in facet_proposals:
        if facet_proposal.path:
            typer.echo(f"- facet {facet_proposal.path}: {facet_proposal.value}")

    if not apply:
        if batch.preview and batch.preview.claim_events:
            _print_claim_preview(batch.preview)
        else:
            _preview_claim_consolidation(root, claim_proposals)
        if batch.preview and batch.preview.interview_prompts:
            typer.echo("Hint: run `aijournal interview` to follow up on the queued prompts.")
        return

    profile_model, claim_models = load_profile_components(root)
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
    write_yaml_model(root / "profile" / "self_profile.yaml", updated_profile)
    write_yaml_model(root / "profile" / "claims.yaml", ClaimsFile(claims=updated_claims))
    _emit_claim_merge_events(merge_events, "Applied claim consolidations:")
    typer.echo(f"Applied {applied} updates from {batch_path}")


@app.command()
def advise(
    question: str = typer.Argument(..., help="Question for the advisor to answer."),
) -> None:
    """Generate advice from the current profile."""
    advice_path = run_advise(question)
    typer.echo(str(advice_path))


@ollama_app.command("health")
def ollama_health() -> None:
    """Inspect Ollama availability for both fake and live modes."""
    if _use_fake_llm():
        models = [
            {"name": "llama3.1:8b-instruct", "size": "8B", "quant": "Q4_K_M"},
            {"name": "llama3.1:70b-instruct", "size": "70B", "quant": "Q4_K_M"},
        ]
        payload = {
            "endpoint": "fake://ollama",
            "default": models[0]["name"],
            "models": models,
        }
        typer.echo(yaml.safe_dump(payload, sort_keys=False).rstrip())
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

    root = Path.cwd()
    config = _load_config(root)
    payload = {
        "endpoint": base,
        "default": build_ollama_config_from_mapping(config).model,
        "models": models_payload,
    }
    typer.echo(yaml.safe_dump(payload, sort_keys=False).rstrip())


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
    root = Path.cwd()
    profile_model, claim_models = load_profile_components(root)
    profile = profile_to_dict(profile_model)
    config = _load_config(root)
    path, changed = run_persona_build(
        profile,
        claim_models,
        config=config,
        root=root,
        token_budget_override=token_budget,
        max_claims_override=max_claims,
        min_claims_override=min_claims,
    )
    status = "Wrote" if changed else "Persona core already up to date"
    typer.echo(f"{status}: {path}")


@persona_app.command("status")
def persona_status() -> None:
    """Check whether persona_core.yaml matches the latest profile edits."""
    root = Path.cwd()
    status, reasons = persona_state(root)
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
    relevant = [event for event in events if event.action != "noop"]
    if not relevant:
        return
    typer.echo(heading)
    for event in relevant:
        if event.action == "created":
            typer.echo(f"  • new claim {event.claim_id}")
        elif event.action == "merged":
            typer.echo(f"  • merged {event.claim_id} (Δstrength {event.delta_strength:+0.2f})")
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
        elif event.action == "scope_split":
            existing_scope = (
                _format_scope_label(event.conflict.signature.scope) if event.conflict else "global"
            )
            related_scope = (
                _format_scope_label(event.related_signature.scope)
                if event.related_signature
                else "global"
            )
            target = event.related_claim_id or "new-claim"
            typer.echo(
                f"  • scope split {event.claim_id} [{existing_scope}] -> {target} [{related_scope}]"
            )


def _preview_claim_consolidation(
    root: Path,
    claim_proposals: Sequence[Any],
) -> None:
    if not claim_proposals:
        return
    _, claim_models = load_profile_components(root)
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
        if outcome.action != "noop":
            events.append(outcome)
    _emit_claim_merge_events(events, "Preview (claim consolidation):")


def _claim_proposal_to_atom(proposal: ClaimProposal, *, timestamp: str) -> ClaimAtom:
    claim_payload = proposal.claim.model_dump(mode="python")
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
        if outcome.action == "noop":
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
        action_literal = cast(
            Literal["created", "merged", "conflict", "scope_split", "noop"],
            outcome.action,
        )
        events.append(
            ClaimPreviewEvent(
                action=action_literal,
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
    events = [event for event in preview.claim_events if event.action != "noop"]
    if events:
        typer.echo("Preview (claim consolidation):")
        for event in events:
            scope_label = _format_scope_label(_scope_tuple_from_payload(event.signature))
            if event.action == "created":
                typer.echo(f"  • new claim {event.claim_id} [{scope_label}]")
            elif event.action == "merged":
                typer.echo(
                    (
                        f"  • merged {event.claim_id} [{scope_label}] "
                        f"(Δstrength {event.delta_strength:+0.2f})"
                    ),
                )
            elif event.action == "scope_split":
                new_scope_label = _format_scope_label(
                    _scope_tuple_from_payload(event.related_signature),
                )
                target = event.related_claim_id or "new-claim"
                action_note = f" ({event.related_action})" if event.related_action else ""
                typer.echo(
                    (
                        f"  • scope split {event.claim_id} [{scope_label}] -> "
                        f"{target} [{new_scope_label}]{action_note}"
                    ),
                )
            elif event.action == "conflict" and event.conflict:
                conflict = event.conflict
                scope_label = _format_scope_label(
                    (
                        conflict.signature.domain,
                        tuple(conflict.signature.context),
                        tuple(conflict.signature.conditions),
                    ),
                )
                typer.secho(
                    (
                        f"  • conflict {event.claim_id} [{scope_label}]: "
                        f"'{conflict.existing_value}' vs '{conflict.incoming_value}'"
                    ),
                    fg=typer.colors.YELLOW,
                )
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
    root = Path.cwd()
    profile_model, claim_models = load_profile_components(root)
    profile = profile_to_dict(profile_model)
    claims = [claim.model_copy(deep=True) for claim in claim_models]
    if not profile and not claims:
        typer.secho("No profile data", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    entries = _load_normalized_entries(root, date)
    if not entries:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    config = _load_config(root)
    weights = config.get("impact_weights", {})

    max_questions = _coaching_max_questions(profile)
    pending_prompts = _collect_pending_interview_prompts(root)
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

    if _use_fake_llm():
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

    root = Path.cwd()
    pending_dir = root / "derived" / "pending" / "profile_updates"
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

    claims_path = root / "profile" / "claims.yaml"
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
