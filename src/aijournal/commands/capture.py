"""Orchestration for the capture command."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Literal

import typer

from aijournal.api.capture import CaptureInput, CaptureRequest
from aijournal.services.capture import CAPTURE_MAX_STAGE, CAPTURE_STAGES, run_capture

if TYPE_CHECKING:
    from pathlib import Path

    from aijournal.common.context import RunContext

CAPTURE_STAGE_LOOKUP = {stage.stage_id: stage for stage in CAPTURE_STAGES}


def run_capture_command(
    ctx: RunContext,
    *,
    from_paths: list[Path] | None,
    text: str | None,
    snapshot: bool,
    source_type: str,
    date: str | None,
    title: str | None,
    tags: list[str],
    projects: list[str],
    mood: str | None,
    apply_profile: str,
    rebuild: str,
    pack: str | None,
    min_stage: int,
    max_stage: int,
    retries: int | None,
    progress: bool,
    dry_run: bool,
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
            "--source-type must be one of: journal, notes, blog.",
            fg=typer.colors.RED,
            err=True,
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
            "--min-stage cannot be greater than --max-stage.",
            fg=typer.colors.RED,
            err=True,
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

    result = run_capture(capture_input, root=ctx.workspace)

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
        ),
    )
