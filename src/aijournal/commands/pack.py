"""Pack command orchestration helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
import yaml

from aijournal.commands.index import _index_settings
from aijournal.commands.ingest import (
    _load_config,
    _relative_source_path,
    _write_yaml_if_changed,
)
from aijournal.commands.persona import ensure_persona_ready_for_pack
from aijournal.pipelines import index as index_pipeline
from aijournal.pipelines import pack as pack_pipeline
from aijournal.utils import time as time_utils


def run_pack(
    level: str,
    date: str | None,
    *,
    output: Path | None,
    max_tokens: int | None,
    fmt: str,
    history_days: int,
    dry_run: bool,
) -> None:
    """Assemble a context bundle for prompting."""
    normalized_level = level.upper()
    fmt_value = fmt.lower()
    if fmt_value not in {"yaml", "json"}:
        typer.secho(f"Unsupported format: {fmt}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if history_days < 0:
        typer.secho("--history-days must be zero or positive.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if normalized_level != "L4" and history_days:
        typer.secho("--history-days is only supported for L4 packs.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    default_budget = {"L1": 1200, "L2": 2000, "L3": 2600, "L4": 3200}
    budget = max_tokens or default_budget.get(normalized_level, 2000)

    root = Path.cwd()
    config = _load_config(root)
    _, _, char_per_token = _index_settings(config)
    ensure_persona_ready_for_pack(root)
    resolved_date = _resolve_pack_date(normalized_level, date, root)

    try:
        entries_info = pack_pipeline.collect_pack_entries(
            root,
            normalized_level,
            resolved_date,
            history_days if normalized_level == "L4" else 0,
        )
    except pack_pipeline.PackAssemblyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    entries_payload: list[pack_pipeline.PackEntry] = []
    for role, path in entries_info:
        text = path.read_text(encoding="utf-8")
        rel = _relative_source_path(path, root)
        tokens = index_pipeline.token_estimate(text, char_per_token)
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
    if total_tokens > budget:
        pack_pipeline.trim_entries(entries_payload, budget, trimmed)
        total_tokens = sum(entry.tokens for entry in entries_payload)

    payload = pack_pipeline.build_pack_payload(
        entries_payload,
        normalized_level,
        resolved_date,
        trimmed,
        total_tokens,
        budget,
    )

    _log_pack_metrics(
        normalized_level,
        total_tokens,
        budget,
        len(trimmed),
        dry_run=dry_run,
        output=output,
    )

    if dry_run:
        typer.echo("Planned files:")
        for entry in entries_payload:
            typer.echo(f"- {entry.path} ({entry.tokens} tokens)")
        if trimmed:
            trimmed_display = ", ".join(f"{item.role}:{item.path}" for item in trimmed)
            typer.echo(f"trimmed: {trimmed_display}")
        return

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        changed = False
        if fmt_value == "json":
            changed = _write_json_if_changed(output, payload.to_dict())
        else:
            changed = _write_yaml_if_changed(output, payload.to_dict())
        if changed:
            typer.echo(str(output))
        else:
            typer.echo("No changes")
        return

    if fmt_value == "json":
        typer.echo(json.dumps(payload.to_dict(), indent=2))
    else:
        typer.echo(yaml.safe_dump(payload.to_dict(), sort_keys=False))


def _write_json_if_changed(path: Path, payload: dict[str, Any]) -> bool:
    existing: dict[str, Any] | None = None
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = None
    if existing == payload:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    return True


def _latest_normalized_day(root: Path) -> str | None:
    base = root / "data" / "normalized"
    if not base.exists():
        return None
    candidates = sorted(p.name for p in base.iterdir() if p.is_dir())
    return candidates[-1] if candidates else None


def _resolve_pack_date(level: str, requested: str | None, root: Path) -> str:
    if requested:
        return requested
    if level == "L1":
        return time_utils.now().strftime("%Y-%m-%d")
    latest = _latest_normalized_day(root)
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


__all__ = [
    "run_pack",
    "_latest_normalized_day",
]
