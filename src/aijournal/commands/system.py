"""System-level health checks and status helpers."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
from pathlib import Path
from typing import Any

import httpx

from aijournal.commands.ingest import _use_fake_llm
from aijournal.commands.persona import persona_state
from aijournal.models import IndexMeta
from aijournal.services.ollama import resolve_ollama_host


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


def _check_annoy_module() -> tuple[bool, str | None]:
    """Return (ok, hint) indicating whether the Annoy module is importable."""

    spec = importlib.util.find_spec("annoy")
    if spec is None:
        return False, "annoy module not importable"
    try:
        # Import to ensure it loads without runtime errors.
        import annoy  # noqa: F401  # pragma: no cover - import side effect only
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)
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
            meta_payload = IndexMeta.model_validate_json(
                meta_path.read_text(encoding="utf-8")
            ).model_dump()
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


def _check_ollama(host_override: str | None = None) -> tuple[bool, dict[str, Any]]:
    if _use_fake_llm():
        return True, {"host": "fake://ollama"}

    host = resolve_ollama_host(host_override)
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

    checks: list[dict[str, Any]] = []
    overall_ok = True

    fts_ok, fts_hint = _check_sqlite_fts5()
    checks.append({"name": "sqlite_fts5", "ok": fts_ok, "hint": fts_hint})
    overall_ok &= fts_ok

    annoy_mod_ok, annoy_mod_hint = _check_annoy_module()
    checks.append({"name": "annoy_module", "ok": annoy_mod_ok, "hint": annoy_mod_hint})
    overall_ok &= annoy_mod_ok

    index_info = _check_index_artifacts(root)
    index_ok = bool(index_info["index_db_exists"] and index_info["annoy_index_exists"])
    checks.append({"name": "index_artifacts", "ok": index_ok, "details": index_info})
    overall_ok &= index_ok

    writable_ok, writable_info = _check_writable_paths(root)
    checks.append({"name": "workspace_writable", "ok": writable_ok, "details": writable_info})
    overall_ok &= writable_ok

    pending_info = _check_pending_updates(root)
    checks.append({"name": "pending_profile_updates", "ok": True, "details": pending_info})

    ollama_ok, ollama_details = _check_ollama(os.getenv("AIJOURNAL_OLLAMA_HOST"))
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
            index_info["meta"] = IndexMeta.model_validate_json(
                meta_path.read_text(encoding="utf-8")
            ).model_dump()
        except Exception as exc:
            index_info["meta_error"] = str(exc)

    pending_info = _check_pending_updates(root)
    host = resolve_ollama_host(os.getenv("AIJOURNAL_OLLAMA_HOST"))

    return {
        "persona": {"status": persona_status, "reasons": persona_reasons},
        "index": index_info,
        "pending_updates": pending_info,
        "ollama": {"host": host},
    }


__all__ = ["run_system_doctor", "run_status_summary"]
