"""System-level health checks and status helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import typer
from pydantic import BaseModel

from aijournal.commands.persona import persona_state
from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.config_loader import load_config, use_fake_llm
from aijournal.common.constants import DEFAULT_OLLAMA_HOST
from aijournal.common.context import RunContext, create_run_context
from aijournal.domain.index import IndexMeta
from aijournal.io.artifacts import load_artifact_data
from aijournal.services.ollama import (
    build_ollama_config_from_mapping,
    resolve_ollama_host,
)
from aijournal.utils.paths import resolve_path

if TYPE_CHECKING:
    from aijournal.common.app_config import AppConfig


def _check_index_artifacts(workspace: Path, config: AppConfig) -> dict[str, Any]:
    index_dir = resolve_path(workspace, config, "derived/index")
    chroma_dir = index_dir / "chroma"
    meta_path = index_dir / "meta.json"

    meta_payload: dict[str, Any] | None = None
    meta_error: str | None = None
    if meta_path.exists():
        try:
            meta_payload = load_artifact_data(meta_path, IndexMeta).model_dump()
        except Exception as exc:
            meta_error = str(exc)

    return {
        "index_dir": str(index_dir),
        "has_chroma_dir": chroma_dir.exists(),
        "chroma_dir": str(chroma_dir),
        "meta_path": str(meta_path),
        "meta": meta_payload,
        "meta_error": meta_error,
    }


def _check_writable_paths(root: Path) -> tuple[bool, dict[str, Any]]:
    rel_paths = [
        "data",
        "derived",
        "profile",
        "derived/index",
        "derived/pending/profile_updates",
    ]
    status: dict[str, Any] = {}
    all_ok = True
    for rel in rel_paths:
        path = root / rel
        exists = path.exists()
        writable = exists and os.access(path, os.W_OK)
        status[rel] = {"exists": exists, "writable": writable}
        if not (exists and writable):
            all_ok = False
    return all_ok, status


def _check_pending_updates(workspace: Path, config: AppConfig) -> dict[str, Any]:
    pending_dir = resolve_path(workspace, config, "derived/pending") / "profile_updates"
    files = sorted(pending_dir.glob("*.yaml")) if pending_dir.exists() else []
    return {
        "count": len(files),
        "samples": [file.name for file in files[:5]],
    }


def _check_ollama(
    config: AppConfig,
    host_override: str | None = None,
    *,
    fake_mode: bool,
) -> tuple[bool, dict[str, Any]]:
    if fake_mode:
        return True, {"host": "fake://ollama"}

    ollama_config = build_ollama_config_from_mapping(config, host=host_override)
    host = ollama_config.host or DEFAULT_OLLAMA_HOST
    try:
        response = httpx.get(f"{host}/api/tags", timeout=15.0)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        return False, {"host": host, "error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive
        return False, {"host": host, "error": str(exc)}

    models = []
    if isinstance(data, dict):
        raw_models = data.get("models")
        if isinstance(raw_models, list):
            for item in raw_models[:5]:
                if isinstance(item, dict):
                    models.append(item.get("name") or item.get("model"))
    return True, {"host": host, "models": models}


def run_system_doctor(workspace: Path, *, fake_mode: bool) -> dict[str, Any]:
    """Run system diagnostics and return a structured payload."""
    config = load_config(workspace)
    checks: list[dict[str, Any]] = []
    overall_ok = True

    index_info = _check_index_artifacts(workspace, config)
    index_ok = bool(index_info["has_chroma_dir"])
    checks.append({"name": "index_artifacts", "ok": index_ok, "details": index_info})
    overall_ok &= index_ok

    writable_ok, writable_info = _check_writable_paths(workspace)
    checks.append({"name": "workspace_writable", "ok": writable_ok, "details": writable_info})
    overall_ok &= writable_ok

    pending_info = _check_pending_updates(workspace, config)
    checks.append({"name": "pending_profile_updates", "ok": True, "details": pending_info})

    ollama_ok, ollama_details = _check_ollama(
        config,
        os.getenv("AIJOURNAL_OLLAMA_HOST"),
        fake_mode=fake_mode,
    )
    checks.append({"name": "ollama_reachable", "ok": ollama_ok, "details": ollama_details})
    overall_ok &= ollama_ok

    persona_status, persona_reasons = persona_state(workspace, workspace, config)
    persona_ok = persona_status == "fresh"
    checks.append(
        {
            "name": "persona_state",
            "ok": persona_ok,
            "details": {"status": persona_status, "reasons": persona_reasons},
        },
    )
    overall_ok &= persona_ok

    return {
        "ok": bool(overall_ok),
        "root": str(workspace),
        "checks": checks,
    }


def run_status_summary(workspace: Path) -> dict[str, Any]:
    """Gather high-level workspace status information."""
    config = load_config(workspace)
    persona_status, persona_reasons = persona_state(workspace, workspace, config)

    index_dir = resolve_path(workspace, config, "derived/index")
    index_info = {
        "has_chroma_dir": (index_dir / "chroma").exists(),
        "meta_path": str(index_dir / "meta.json"),
        "meta": None,
        "meta_error": None,
    }
    meta_path = index_dir / "meta.json"
    if meta_path.exists():
        try:
            index_info["meta"] = load_artifact_data(meta_path, IndexMeta).model_dump()
        except Exception as exc:
            index_info["meta_error"] = str(exc)

    pending_info = _check_pending_updates(workspace, config)
    config_host = config.host
    host = resolve_ollama_host(
        os.getenv("AIJOURNAL_OLLAMA_HOST"),
        config_host=str(config_host) if config_host else None,
    )

    return {
        "persona": {"status": persona_status, "reasons": persona_reasons},
        "index": index_info,
        "pending_updates": pending_info,
        "ollama": {
            "host": host,
            "config_host": config_host,
        },
    }


class SystemDoctorOptions(BaseModel):
    """Options for the system doctor command."""


@dataclass(slots=True)
class SystemDoctorPrepared:
    pass


@dataclass(slots=True)
class SystemDoctorResult:
    diagnostics: dict[str, Any]


class SystemStatusOptions(BaseModel):
    """Options for the system status command."""


@dataclass(slots=True)
class SystemStatusPrepared:
    pass


@dataclass(slots=True)
class SystemStatusResult:
    summary: dict[str, Any]


def run_system_doctor_cli(workspace: Path | None = None) -> None:
    workspace = workspace or Path.cwd()
    config = load_config(workspace)
    ctx = create_run_context(
        command="ops.system.doctor",
        workspace=workspace,
        config=config,
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )

    def _prepare(_: RunContext, __: SystemDoctorOptions) -> SystemDoctorPrepared:
        return SystemDoctorPrepared()

    def _invoke(inner_ctx: RunContext, __: SystemDoctorPrepared) -> SystemDoctorResult:
        diagnostics = run_system_doctor(inner_ctx.workspace, fake_mode=inner_ctx.use_fake_llm)
        inner_ctx.emit(event="pipeline_complete", ok=diagnostics.get("ok", False))
        return SystemDoctorResult(diagnostics=diagnostics)

    def _persist(_: RunContext, result: SystemDoctorResult) -> None:
        diagnostics = result.diagnostics
        typer.echo("System diagnostics:\n")
        for check in diagnostics.get("checks", []):
            ok = bool(check.get("ok"))
            color = typer.colors.GREEN if ok else typer.colors.RED
            status_text = "ok" if ok else "failed"
            typer.secho(f"{check.get('name')}: {status_text}", fg=color)
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
        typer.echo(json.dumps(diagnostics, indent=2, ensure_ascii=False))

        if not diagnostics.get("ok", False):
            raise typer.Exit(1)

    run_command_pipeline(
        ctx,
        SystemDoctorOptions(),
        prepare_inputs=_prepare,
        invoke_pipeline=_invoke,
        persist_output=_persist,
    )


def run_system_status_cli(workspace: Path | None = None) -> None:
    workspace = workspace or Path.cwd()
    config = load_config(workspace)
    ctx = create_run_context(
        command="ops.system.status",
        workspace=workspace,
        config=config,
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )

    def _prepare(_: RunContext, __: SystemStatusOptions) -> SystemStatusPrepared:
        return SystemStatusPrepared()

    def _invoke(inner_ctx: RunContext, __: SystemStatusPrepared) -> SystemStatusResult:
        summary = run_status_summary(inner_ctx.workspace)
        inner_ctx.emit(
            event="pipeline_complete",
            persona_status=summary.get("persona", {}).get("status"),
        )
        return SystemStatusResult(summary=summary)

    def _persist(persist_ctx: RunContext, result: SystemStatusResult) -> None:
        summary = result.summary
        persona = summary.get("persona", {})
        persona_status = persona.get("status")
        persona_reasons = persona.get("reasons", [])
        index_info = summary.get("index", {})
        pending = summary.get("pending_updates", {})
        ollama = summary.get("ollama", {})

        exit_code = 0
        color = typer.colors.GREEN if persona_status == "fresh" else typer.colors.YELLOW
        typer.secho(f"Persona status: {persona_status}", fg=color)
        if persona_status != "fresh":
            exit_code = 1
            for reason in persona_reasons:
                typer.echo(f"  - {reason}")

        index_messages: list[str] = []
        if index_info.get("has_chroma_dir"):
            typer.secho("Index artifacts: present", fg=typer.colors.GREEN)
        else:
            typer.secho("Index artifacts: missing", fg=typer.colors.RED)
            exit_code = 1
        meta = index_info.get("meta") or {}
        meta_error = index_info.get("meta_error")
        if meta_error:
            typer.secho(f"  meta error: {meta_error}", fg=typer.colors.RED)
            exit_code = 1
        elif isinstance(meta, dict):
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

        pending_count = pending.get("count", 0)
        if pending_count:
            typer.secho(
                f"Pending profile updates: {pending_count}",
                fg=typer.colors.YELLOW,
            )
            for name in pending.get("samples", []):
                typer.echo(f"  - {name}")
        else:
            typer.secho("Pending profile updates: none", fg=typer.colors.GREEN)

        typer.echo(
            f"Ollama host: {ollama.get('host')}"
            + (" (fake mode)" if persist_ctx.use_fake_llm else ""),
        )
        typer.echo("Run `aijournal ops system doctor` for detailed diagnostics.")

        if exit_code:
            raise typer.Exit(exit_code)

    run_command_pipeline(
        ctx,
        SystemStatusOptions(),
        prepare_inputs=_prepare,
        invoke_pipeline=_invoke,
        persist_output=_persist,
    )
