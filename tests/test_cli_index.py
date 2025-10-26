"""Tests for `aijournal index` commands (rebuild + tail)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from aijournal.cli import app
from tests.helpers import read_index_meta, write_manifest, write_normalized_entry

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def _has_index_group() -> bool:
    return any(info.name == "index" for info in app.registered_commands)


@pytest.fixture(autouse=True)
def _skip_if_missing_index() -> None:
    if not _has_index_group():
        pytest.skip("index command group not available yet")


def test_index_rebuild_creates_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    date = "2025-02-03"
    entry_id = "2025-02-03-focus-notes"
    write_normalized_entry(
        tmp_path,
        date=date,
        entry_id=entry_id,
        summary="Protected two focus blocks",
    )
    write_manifest(
        tmp_path,
        [
            {
                "id": entry_id,
                "hash": f"hash-{entry_id}",
                "source_type": "journal",
            },
        ],
    )

    env = {"AIJOURNAL_FAKE_OLLAMA": "1"}
    result = runner.invoke(app, ["index", "rebuild"], env=env)
    assert result.exit_code == 0, result.stdout

    db_path = tmp_path / "derived" / "index" / "index.db"
    chunk_dir = tmp_path / "derived" / "index" / "chunks"
    meta_path = tmp_path / "derived" / "index" / "meta.json"

    assert db_path.exists()
    assert meta_path.exists()
    assert (chunk_dir / f"{date}.yaml").exists()
    assert (chunk_dir / f"{date}.npy").exists()

    with sqlite3.connect(db_path) as conn:
        chunk_total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert chunk_total > 0

    meta = read_index_meta(tmp_path)
    assert meta["chunk_count"] == chunk_total
    assert meta["mode"] == "rebuild"


def test_index_tail_indexes_new_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    env = {"AIJOURNAL_FAKE_OLLAMA": "1"}

    day_one = "2025-02-03"
    entry_one = "2025-02-03-focus-notes"
    write_normalized_entry(
        tmp_path,
        date=day_one,
        entry_id=entry_one,
        summary="Morning plan for focus",
    )

    manifest_entries = [
        {"id": entry_one, "hash": f"hash-{entry_one}", "source_type": "journal"},
    ]
    write_manifest(tmp_path, manifest_entries)

    rebuild = runner.invoke(app, ["index", "rebuild"], env=env)
    assert rebuild.exit_code == 0, rebuild.stdout
    initial_meta = read_index_meta(tmp_path)
    initial_chunks = initial_meta["chunk_count"]

    day_two = "2025-02-04"
    entry_two = "2025-02-04-reflection"
    write_normalized_entry(
        tmp_path,
        date=day_two,
        entry_id=entry_two,
        summary="Reflection on focus guardrails",
    )
    manifest_entries.append(
        {"id": entry_two, "hash": f"hash-{entry_two}", "source_type": "journal"},
    )
    write_manifest(tmp_path, manifest_entries)

    tail = runner.invoke(app, ["index", "tail", "--since", day_two], env=env)
    assert tail.exit_code == 0, tail.stdout

    meta = read_index_meta(tmp_path)
    assert meta["mode"] == "tail"
    assert meta["chunk_count"] > initial_chunks
    chunk_dir = tmp_path / "derived" / "index" / "chunks"
    assert (chunk_dir / f"{day_two}.yaml").exists()
    assert (chunk_dir / f"{day_two}.npy").exists()
