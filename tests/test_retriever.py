"""Tests for the shared Retriever service."""

from __future__ import annotations

import json
import shutil
import threading
from typing import TYPE_CHECKING

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app
from aijournal.common.app_config import AppConfig
from aijournal.domain.index import IndexMeta
from aijournal.io.artifacts import load_artifact_data
from aijournal.services.retriever import RetrievalFilters, Retriever
from tests.helpers import copy_fixture_workspace, write_manifest, write_normalized_entry

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fake_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")


def _bootstrap_index(tmp_path: Path, *, day: str, entry_id: str, summary: str) -> None:
    write_normalized_entry(
        tmp_path,
        date=day,
        entry_id=entry_id,
        summary=summary,
    )
    write_manifest(
        tmp_path,
        [
            {"id": entry_id, "hash": f"hash-{entry_id}", "source_type": "journal"},
        ],
    )
    result = runner.invoke(
        app,
        ["ops", "index", "rebuild"],
        env={"AIJOURNAL_FAKE_OLLAMA": "1"},
    )
    assert result.exit_code == 0, result.stdout
    meta_path = tmp_path / "derived" / "index" / "meta.json"
    assert meta_path.exists(), "Expected index meta artifact to be written"
    meta = load_artifact_data(meta_path, IndexMeta)
    assert meta.embedding_model is not None


def test_retriever_parity_with_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    copy_fixture_workspace("miniwk", workspace)
    monkeypatch.chdir(workspace)

    result = runner.invoke(
        app,
        ["ops", "index", "rebuild"],
        env={"AIJOURNAL_FAKE_OLLAMA": "1"},
    )
    assert result.exit_code == 0, result.stdout
    meta_path = workspace / "derived" / "index" / "meta.json"
    assert meta_path.exists()

    spec = json.loads((workspace / "expected_retrieval.json").read_text(encoding="utf-8"))
    config_dict = yaml.safe_load((workspace / "config.yaml").read_text(encoding="utf-8"))
    config = AppConfig.model_validate(config_dict)

    retriever = Retriever(workspace, config)
    top = int(spec.get("top") or len(spec["expected_chunk_ids"]))
    result = retriever.search(spec["query"], k=top)
    chunk_ids = [chunk.chunk_id for chunk in result.chunks[: len(spec["expected_chunk_ids"])]]

    assert chunk_ids == spec["expected_chunk_ids"], (
        f"Chunk IDs {chunk_ids!r} do not match expected {spec['expected_chunk_ids']!r}."
    )
    retriever.close()


def test_retriever_returns_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0, init_result.stdout
    day = "2025-02-03"
    entry_id = "2025-02-03-focus-notes"
    _bootstrap_index(
        tmp_path,
        day=day,
        entry_id=entry_id,
        summary="Protected two focus blocks",
    )

    config_dict = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    config = AppConfig.model_validate(config_dict)
    retriever = Retriever(tmp_path, config)
    result = retriever.search("focus blocks", k=3)

    assert result.meta.mode == "chroma"
    assert result.chunks
    assert result.chunks[0].normalized_id == entry_id
    retriever.close()


def test_retriever_errors_when_index_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0, init_result.stdout
    day = "2025-02-04"
    entry_id = "2025-02-04-reflection"
    _bootstrap_index(
        tmp_path,
        day=day,
        entry_id=entry_id,
        summary="Reflection on focus guardrails",
    )

    index_dir = tmp_path / "derived" / "index"
    chroma_dir = index_dir / "chroma"
    if chroma_dir.exists():
        shutil.rmtree(chroma_dir)

    config_dict = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    config = AppConfig.model_validate(config_dict)
    retriever = Retriever(tmp_path, config)
    filters = RetrievalFilters(tags=frozenset({"focus"}))
    with pytest.raises(
        RuntimeError,
        match="Retrieval index not available",
    ):
        retriever.search("reflection", k=1, filters=filters)
    retriever.close()


def test_retriever_close_from_different_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0, init_result.stdout
    day = "2025-02-05"
    entry_id = "2025-02-05-focus-notes"
    _bootstrap_index(
        tmp_path,
        day=day,
        entry_id=entry_id,
        summary="Captured focus rituals",
    )

    config_dict = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    config = AppConfig.model_validate(config_dict)
    retriever = Retriever(tmp_path, config)

    # Opening a connection in the main thread
    result = retriever.search("focus", k=1)
    assert result.chunks

    errors: list[BaseException] = []

    def _close() -> None:
        try:
            retriever.close()
        except BaseException as exc:  # pragma: no cover - diagnostic
            errors.append(exc)

    thread = threading.Thread(target=_close)
    thread.start()
    thread.join()

    assert not errors, f"Unexpected errors closing retriever: {errors!r}"
