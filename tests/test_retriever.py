"""Tests for the shared Retriever service."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app
from aijournal.services.retriever import RetrievalFilters, Retriever
from tests.helpers import write_manifest, write_normalized_entry

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
    result = runner.invoke(app, ["index", "rebuild"], env={"AIJOURNAL_FAKE_OLLAMA": "1"})
    assert result.exit_code == 0, result.stdout


def test_retriever_annoy_mode_returns_chunks(
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

    config = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))
    retriever = Retriever(tmp_path, config)
    result = retriever.search("focus blocks", k=3)

    assert result.meta["mode"] == "annoy"
    assert result.chunks
    assert result.chunks[0].normalized_id == entry_id
    retriever.close()


def test_retriever_fallback_mode_when_index_missing(
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
    (index_dir / "index.db").unlink()
    (index_dir / "annoy.index").unlink()

    config = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))
    retriever = Retriever(tmp_path, config)
    filters = RetrievalFilters(tags=frozenset({"focus"}))
    result = retriever.search("reflection", k=1, filters=filters)

    assert result.meta["mode"] == "fake(fallback)"
    assert result.chunks
    assert result.chunks[0].normalized_id == entry_id
    retriever.close()
