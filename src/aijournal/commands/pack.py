"""Pack command orchestration helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from pydantic import BaseModel

from aijournal.commands.index import _index_settings
from aijournal.commands.ingest import _relative_source_path
from aijournal.commands.persona import ensure_persona_ready_for_pack
from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.config_loader import load_config, use_fake_llm
from aijournal.common.context import RunContext, create_run_context
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.packs import PackBundle
from aijournal.io.artifacts import save_artifact
from aijournal.io.yaml_io import dump_yaml
from aijournal.pipelines import index as index_pipeline
from aijournal.pipelines import pack as pack_pipeline
from aijournal.utils import time as time_utils
from aijournal.utils.paths import resolve_path

if TYPE_CHECKING:
    from aijournal.common.app_config import AppConfig


class PackOptions(BaseModel):
    level: str
    date: str | None = None
    output: Path | None = None
    max_tokens: int | None = None
    fmt: str = "yaml"
    history_days: int = 0
    dry_run: bool = False


@dataclass(slots=True)
class PackPrepared:
    normalized_level: str
    resolved_date: str
    fmt_value: str
    history_days: int
    budget: int
    output: Path | None
    dry_run: bool
    char_per_token: float


@dataclass(slots=True)
class PackResult:
    bundle: PackBundle
    trimmed: list[pack_pipeline.TrimmedFile]
    entries: list[pack_pipeline.PackEntry]
    total_tokens: int
    budget: int
    fmt_value: str
    output: Path | None
    dry_run: bool


def run_pack(
    level: str,
    date: str | None,
    *,
    output: Path | None,
    max_tokens: int | None,
    fmt: str,
    history_days: int,
    dry_run: bool,
    workspace: Path | None = None,
) -> None:
    """Assemble a context bundle for prompting."""
    workspace = workspace or Path.cwd()
    config = load_config(workspace)
    ctx = create_run_context(
        command="pack",
        workspace=workspace,
        config=config,
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )
    options = PackOptions(
        level=level,
        date=date,
        output=output,
        max_tokens=max_tokens,
        fmt=fmt,
        history_days=history_days,
        dry_run=dry_run,
    )
    run_pack_command(ctx, options)


def prepare_inputs(ctx: RunContext, options: PackOptions) -> PackPrepared:
    normalized_level = options.level.upper()
    fmt_value = options.fmt.lower()
    if fmt_value not in {"yaml", "json"}:
        typer.secho(f"Unsupported format: {options.fmt}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if options.history_days < 0:
        typer.secho("--history-days must be zero or positive.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if normalized_level != "L4" and options.history_days:
        typer.secho("--history-days is only supported for L4 packs.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    default_budget = {"L1": 1200, "L2": 2000, "L3": 2600, "L4": 3200}
    budget = options.max_tokens or default_budget.get(normalized_level, 2000)
    ensure_persona_ready_for_pack(ctx.workspace, ctx.workspace, ctx.config)
    resolved_date = _resolve_pack_date(normalized_level, options.date, ctx.workspace, ctx.config)

    _, char_per_token = _index_settings(ctx.config)

    ctx.emit(
        event="prepare_summary",
        level=normalized_level,
        date=resolved_date,
        budget=budget,
        history_days=options.history_days,
    )
    return PackPrepared(
        normalized_level=normalized_level,
        resolved_date=resolved_date,
        fmt_value=fmt_value,
        history_days=options.history_days,
        budget=budget,
        output=options.output,
        dry_run=options.dry_run,
        char_per_token=char_per_token,
    )


def invoke_pipeline(ctx: RunContext, prepared: PackPrepared) -> PackResult:
    try:
        entries_info = pack_pipeline.collect_pack_entries(
            ctx.workspace,
            prepared.normalized_level,
            prepared.resolved_date,
            prepared.history_days if prepared.normalized_level == "L4" else 0,
        )
    except pack_pipeline.PackAssemblyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    entries_payload: list[pack_pipeline.PackEntry] = []
    for role, path in entries_info:
        text = path.read_text(encoding="utf-8")
        rel = _relative_source_path(path, ctx.workspace)
        tokens = index_pipeline.token_estimate(text, prepared.char_per_token)
        entries_payload.append(
            pack_pipeline.PackEntry(
                role=role,
                path=rel,
                tokens=tokens,
                content=text,
            ),
        )

    total_tokens = sum(entry.tokens for entry in entries_payload)
    trimmed: list[pack_pipeline.TrimmedFile] = []
    if total_tokens > prepared.budget:
        pack_pipeline.trim_entries(entries_payload, prepared.budget, trimmed)
        total_tokens = sum(entry.tokens for entry in entries_payload)

    bundle = pack_pipeline.build_pack_payload(
        entries_payload,
        prepared.normalized_level,
        prepared.resolved_date,
        trimmed,
        total_tokens,
        prepared.budget,
    )

    ctx.emit(
        event="pipeline_complete",
        level=prepared.normalized_level,
        total_tokens=total_tokens,
        trimmed=len(trimmed),
    )
    return PackResult(
        bundle=bundle,
        trimmed=trimmed,
        entries=entries_payload,
        total_tokens=total_tokens,
        budget=prepared.budget,
        fmt_value=prepared.fmt_value,
        output=prepared.output,
        dry_run=prepared.dry_run,
    )


def persist_output(ctx: RunContext, result: PackResult) -> None:
    _log_pack_metrics(
        result.bundle.level,
        result.total_tokens,
        result.budget,
        len(result.trimmed),
        dry_run=result.dry_run,
        output=result.output,
    )

    artifact_kind_map = {
        "L1": ArtifactKind.PACK_L1,
        "L2": ArtifactKind.PACK_L2,
        "L3": ArtifactKind.PACK_L3,
        "L4": ArtifactKind.PACK_L4,
    }
    artifact_meta = ArtifactMeta(
        created_at=result.bundle.meta.generated_at,
        notes={"level": result.bundle.level, "date": result.bundle.date},
    )
    artifact = Artifact[PackBundle](
        kind=artifact_kind_map[result.bundle.level],
        meta=artifact_meta,
        data=result.bundle,
    )

    if result.dry_run:
        typer.echo("Planned files:")
        for entry in result.bundle.files:
            typer.echo(f"- {entry.path} ({entry.tokens} tokens)")
        if result.trimmed:
            trimmed_display = ", ".join(f"{item.role}:{item.path}" for item in result.trimmed)
            typer.echo(f"trimmed: {trimmed_display}")
        return

    if result.output:
        result.output.parent.mkdir(parents=True, exist_ok=True)
        previous = result.output.read_text(encoding="utf-8") if result.output.exists() else None
        save_artifact(result.output, artifact, format=result.fmt_value)
        new_text = result.output.read_text(encoding="utf-8") if result.output.exists() else None
        changed = previous != new_text
        if changed:
            typer.echo(str(result.output))
        else:
            typer.echo("No changes")
        return

    artifact_payload = artifact.model_dump(mode="json")
    if result.fmt_value == "json":
        typer.echo(json.dumps(artifact_payload, indent=2))
    else:
        typer.echo(dump_yaml(artifact_payload, sort_keys=False))


def run_pack_command(ctx: RunContext, options: PackOptions) -> None:
    run_command_pipeline(
        ctx,
        options,
        prepare_inputs=prepare_inputs,
        invoke_pipeline=invoke_pipeline,
        persist_output=persist_output,
    )


def _latest_normalized_day(workspace: Path, config: AppConfig) -> str | None:
    base = resolve_path(workspace, config, "data/normalized")
    if not base.exists():
        return None
    candidates = sorted(p.name for p in base.iterdir() if p.is_dir())
    return candidates[-1] if candidates else None


def _resolve_pack_date(level: str, requested: str | None, root: Path, config: AppConfig) -> str:
    if requested:
        return requested
    if level == "L1":
        return time_utils.now().strftime("%Y-%m-%d")
    latest = _latest_normalized_day(root, config)
    if latest:
        return latest
    typer.secho("No normalized entries available; provide --date.", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _log_pack_metrics(
    level: str,
    total_tokens: int,
    budget: int,
    trimmed_count: int,
    *,
    dry_run: bool,
    output: Path | None,
) -> None:
    payload = {
        "event": "pack.telemetry",
        "level": level,
        "total_tokens": total_tokens,
        "budget": budget,
        "trimmed": trimmed_count,
        "dry_run": dry_run,
        "output": str(output) if output else None,
    }
    typer.echo(json.dumps(payload, ensure_ascii=False), err=True)
