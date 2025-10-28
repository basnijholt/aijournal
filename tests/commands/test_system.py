"""Tests for system doctor and status helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aijournal.commands import system


def test_run_system_doctor_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")

    monkeypatch.setattr(system, "_check_sqlite_fts5", lambda: (True, None))
    monkeypatch.setattr(
        system,
        "_check_index_artifacts",
        lambda root: {
            "index_db_exists": True,
            "annoy_index_exists": True,
            "meta": {"chunk_count": 1},
            "meta_error": None,
        },
    )
    monkeypatch.setattr(system, "_check_writable_paths", lambda root: (True, {}))
    monkeypatch.setattr(system, "_check_pending_updates", lambda root: {"count": 0, "samples": []})
    monkeypatch.setattr(system, "_check_ollama", lambda host: (True, {"host": "fake://ollama"}))
    monkeypatch.setattr(system, "persona_state", lambda root: ("fresh", []))

    result = system.run_system_doctor(tmp_path)

    assert result["ok"] is True
    names = [check["name"] for check in result["checks"]]
    assert "sqlite_fts5" in names
    assert "ollama_reachable" in names


def test_run_status_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_OLLAMA_HOST", "http://127.0.0.1:11434")
    monkeypatch.setattr(system, "persona_state", lambda root: ("fresh", []))

    index_dir = tmp_path / "derived" / "index"
    index_dir.mkdir(parents=True)
    (index_dir / "index.db").touch()
    (index_dir / "annoy.index").touch()
    meta_path = index_dir / "meta.json"
    meta_payload = {
        "embedding_model": "nomic-embed-text",
        "vector_dimension": 384,
        "chunk_count": 2,
        "entry_count": 2,
        "mode": "rebuild",
        "fake_mode": True,
        "annoy_trees": 50,
        "search_k_factor": 3.0,
        "char_per_token": 4.2,
        "touched_dates": ["2025-10-28"],
        "updated_at": "2025-10-28T00:00:00Z",
    }
    meta_path.write_text(json.dumps(meta_payload), encoding="utf-8")

    pending_dir = tmp_path / "derived" / "pending" / "profile_updates"
    pending_dir.mkdir(parents=True)
    for idx in range(3):
        (pending_dir / f"batch-{idx}.yaml").write_text("batch", encoding="utf-8")

    summary = system.run_status_summary(tmp_path)

    assert summary["persona"]["status"] == "fresh"
    assert summary["index"]["has_index_db"] is True
    assert summary["index"]["has_annoy_index"] is True
    assert summary["index"]["meta"]["chunk_count"] == 2
    assert summary["pending_updates"]["count"] == 3
    assert summary["ollama"]["host"].startswith("http")
