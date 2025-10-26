"""CLI coverage for retrieval index search commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from aijournal.cli import app
from tests.helpers import write_manifest, write_normalized_entry

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fake_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")


def _init_workspace(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(base)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0, init_result.stdout


def _build_index(
    base: Path,
    *,
    day: str,
    entry_id: str,
    summary: str,
    tags: list[str] | None = None,
    source_type: str = "journal",
) -> None:
    write_normalized_entry(
        base,
        date=day,
        entry_id=entry_id,
        summary=summary,
        tags=tags,
        source_type=source_type,
    )
    write_manifest(
        base,
        [
            {"id": entry_id, "hash": f"hash-{entry_id}", "source_type": source_type},
        ],
    )
    rebuild = runner.invoke(
        app,
        ["index", "rebuild"],
        env={"AIJOURNAL_FAKE_OLLAMA": "1"},
    )
    assert rebuild.exit_code == 0, rebuild.stdout


def test_index_search_returns_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    day = "2025-02-03"
    entry_id = "2025-02-03-focus-notes"
    _build_index(
        tmp_path,
        day=day,
        entry_id=entry_id,
        summary="Protected two focus blocks and captured deep work ideas.",
        tags=["focus", "planning"],
    )

    result = runner.invoke(
        app,
        ["index", "search", "deep work ideas", "--tags", "focus", "--top", "3"],
    )
    assert result.exit_code == 0, result.stdout
    assert "Top" in result.stdout
    assert "fake mode" in result.stdout
    assert "focus" in result.stdout
    assert "deep work ideas" in result.stdout


def test_index_search_handles_no_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _build_index(
        tmp_path,
        day="2025-02-03",
        entry_id="2025-02-03-focus-notes",
        summary="Protected two focus blocks and captured deep work ideas.",
    )

    result = runner.invoke(
        app,
        ["index", "search", "nonexistent topic", "--tags", "missing-tag"],
    )
    assert result.exit_code == 0
    assert "No matches found." in result.stdout


def test_index_search_errors_when_index_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["index", "search", "anything"])
    assert result.exit_code != 0
    assert "Retrieval index not available" in (result.stderr or "")
