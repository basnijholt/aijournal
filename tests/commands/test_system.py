"""Tests for system doctor and status helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aijournal.commands import system
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.index import IndexMeta
from aijournal.io.artifacts import save_artifact

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_run_system_doctor_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")

    monkeypatch.setattr(
        system,
        "_check_index_artifacts",
        lambda workspace, config: {
            "has_chroma_dir": True,
            "meta": {"chunk_count": 1},
            "meta_error": None,
        },
    )
    monkeypatch.setattr(system, "_check_writable_paths", lambda root: (True, {}))
    monkeypatch.setattr(
        system,
        "_check_pending_updates",
        lambda workspace, config: {"count": 0, "samples": []},
    )
    monkeypatch.setattr(
        system,
        "_check_ollama",
        lambda config, host, fake_mode: (True, {"host": "fake://ollama"}),
    )
    monkeypatch.setattr(system, "persona_state", lambda root, workspace, config: ("fresh", []))

    result = system.run_system_doctor(tmp_path, fake_mode=True)

    assert result["ok"] is True
    names = [check["name"] for check in result["checks"]]
    assert "ollama_reachable" in names


def test_run_status_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_OLLAMA_HOST", "http://127.0.0.1:11434")
    monkeypatch.setattr(system, "persona_state", lambda root, workspace, config: ("fresh", []))

    index_dir = tmp_path / "derived" / "index"
    index_dir.mkdir(parents=True)
    (index_dir / "chroma").mkdir()
    meta_path = index_dir / "meta.json"
    meta_payload = {
        "embedding_model": "embeddinggemma:300m",
        "vector_dimension": 384,
        "chunk_count": 2,
        "entry_count": 2,
        "mode": "rebuild",
        "fake_mode": True,
        "search_k_factor": 3.0,
        "char_per_token": 4.2,
        "touched_dates": ["2025-10-28"],
        "updated_at": "2025-10-28T00:00:00Z",
    }
    index_meta = IndexMeta(**meta_payload)
    save_artifact(
        meta_path,
        Artifact[IndexMeta](
            kind=ArtifactKind.INDEX_META,
            meta=ArtifactMeta(
                created_at=meta_payload["updated_at"],
                model=meta_payload["embedding_model"],
            ),
            data=index_meta,
        ),
        format="json",
    )

    pending_dir = tmp_path / "derived" / "pending" / "profile_updates"
    pending_dir.mkdir(parents=True)
    for idx in range(3):
        (pending_dir / f"batch-{idx}.yaml").write_text("batch", encoding="utf-8")

    # Configure WorkspacePaths for the test
    summary = system.run_status_summary(tmp_path)

    assert summary["persona"]["status"] == "fresh"
    assert summary["index"]["has_chroma_dir"] is True
    assert summary["index"]["meta"]["chunk_count"] == 2
    assert summary["pending_updates"]["count"] == 3
    assert summary["ollama"]["host"].startswith("http")
