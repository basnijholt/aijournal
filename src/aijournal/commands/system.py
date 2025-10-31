"""System-level health checks and status helpers."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import typer
from pydantic import BaseModel

from aijournal.commands.ingest import _load_config, _use_fake_llm
from aijournal.commands.persona import persona_state
from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.context import RunContext, create_run_context
from aijournal.domain.index import IndexMeta
from aijournal.io.artifacts import load_artifact_data
from aijournal.services.ollama import (
    DEFAULT_OLLAMA_HOST,
    build_ollama_config_from_mapping,
    resolve_ollama_host,
)


def _check_sqlite_fts5() -> tuple[bool, str | None]:
    """Return (ok, hint) indicating whether SQLite has FTS5 support."""

    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE __fts5_test USING fts5(content)")
        conn.execute("DROP TABLE IF EXISTS __fts5_test")
    except sqlite3.OperationalError as exc:  # pragma: no cover - depends on python build
        return False, str(exc)
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - defensive
            pass
    return True, None


def _check_index_artifacts(root: Path) -> dict[str, Any]:
    index_dir = root / "derived" / "index"
    db_path = index_dir / "index.db"
    annoy_path = index_dir / "annoy.index"
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
        "index_db_exists": db_path.exists(),
        "annoy_index_exists": annoy_path.exists(),
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


def _check_pending_updates(root: Path) -> dict[str, Any]:
    pending_dir = root / "derived" / "pending" / "profile_updates"
    files = sorted(pending_dir.glob("*.yaml")) if pending_dir.exists() else []
    return {
        "count": len(files),
        "samples": [file.name for file in files[:5]],
    }


def _check_ollama(
    config: Mapping[str, Any],
    host_override: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    if _use_fake_llm():
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


def run_system_doctor(root: Path) -> dict[str, Any]:
    """Run system diagnostics and return a structured payload."""

    config = _load_config(root)
    checks: list[dict[str, Any]] = []
    overall_ok = True

    fts_ok, fts_hint = _check_sqlite_fts5()
    checks.append({"name": "sqlite_fts5", "ok": fts_ok, "hint": fts_hint})
    overall_ok &= fts_ok

    index_info = _check_index_artifacts(root)
    index_ok = bool(index_info["index_db_exists"] and index_info["annoy_index_exists"])
    checks.append({"name": "index_artifacts", "ok": index_ok, "details": index_info})
    overall_ok &= index_ok

    writable_ok, writable_info = _check_writable_paths(root)
    checks.append({"name": "workspace_writable", "ok": writable_ok, "details": writable_info})
    overall_ok &= writable_ok

    pending_info = _check_pending_updates(root)
    checks.append({"name": "pending_profile_updates", "ok": True, "details": pending_info})

    ollama_ok, ollama_details = _check_ollama(config, os.getenv("AIJOURNAL_OLLAMA_HOST"))
    checks.append({"name": "ollama_reachable", "ok": ollama_ok, "details": ollama_details})
    overall_ok &= ollama_ok

    persona_status, persona_reasons = persona_state(root)
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
        "root": str(root),
        "checks": checks,
    }


def run_status_summary(root: Path) -> dict[str, Any]:
    """Gather high-level workspace status information."""

    config = _load_config(root)
    persona_status, persona_reasons = persona_state(root)

    index_dir = root / "derived" / "index"
    index_info = {
        "has_index_db": (index_dir / "index.db").exists(),
        "has_annoy_index": (index_dir / "annoy.index").exists(),
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

    pending_info = _check_pending_updates(root)
    config_host = config.get("host") if isinstance(config, Mapping) else None
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


def run_system_doctor_cli() -> None:
    root = Path.cwd()
    config = _load_config(root)
    ctx = create_run_context(
        command="ops.system.doctor",
        root=root,
        config=config,
        use_fake_llm=_use_fake_llm(),
        trace=False,
        verbose_json=False,
    )

    def _prepare(_: RunContext, __: SystemDoctorOptions) -> SystemDoctorPrepared:
        return SystemDoctorPrepared()

    def _invoke(inner_ctx: RunContext, __: SystemDoctorPrepared) -> SystemDoctorResult:
        diagnostics = run_system_doctor(inner_ctx.root)
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


def run_system_status_cli() -> None:
    root = Path.cwd()
    config = _load_config(root)
    ctx = create_run_context(
        command="ops.system.status",
        root=root,
        config=config,
        use_fake_llm=_use_fake_llm(),
        trace=False,
        verbose_json=False,
    )

    def _prepare(_: RunContext, __: SystemStatusOptions) -> SystemStatusPrepared:
        return SystemStatusPrepared()

    def _invoke(inner_ctx: RunContext, __: SystemStatusPrepared) -> SystemStatusResult:
        summary = run_status_summary(inner_ctx.root)
        inner_ctx.emit(
            event="pipeline_complete",
            persona_status=summary.get("persona", {}).get("status"),
        )
        return SystemStatusResult(summary=summary)

    def _persist(_: RunContext, result: SystemStatusResult) -> None:
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
        if index_info.get("has_index_db") and index_info.get("has_annoy_index"):
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
            chunk_count = meta.get("chunk_total")
            entry_count = meta.get("entry_total")
            updated_at = meta.get("generated_at")
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
            f"Ollama host: {ollama.get('host')}" + (" (fake mode)" if _use_fake_llm() else "")
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
