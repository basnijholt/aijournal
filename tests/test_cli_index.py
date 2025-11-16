"""CLI coverage for retrieval index search commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from aijournal.cli import app
from tests.helpers import write_manifest, write_normalized_entry

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def _fake_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")


def _build_index(
    base: Path,
    cli_runner: CliRunner,
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
    rebuild = cli_runner.invoke(app, ["ops", "index", "rebuild"])
    assert rebuild.exit_code == 0, rebuild.stdout


def test_index_search_returns_results(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    day = "2025-02-03"
    entry_id = "2025-02-03-focus-notes"
    _build_index(
        cli_workspace,
        cli_runner,
        day=day,
        entry_id=entry_id,
        summary="Protected two focus blocks and captured deep work ideas.",
        tags=["focus", "planning"],
    )

    result = cli_runner.invoke(
        app,
        [
            "ops",
            "index",
            "search",
            "deep work ideas",
            "--tags",
            "focus",
            "--top",
            "3",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "Top" in result.stdout
    assert "fake mode" in result.stdout
    assert "focus" in result.stdout
    assert "deep work ideas" in result.stdout


def test_index_search_handles_no_matches(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _build_index(
        cli_workspace,
        cli_runner,
        day="2025-02-03",
        entry_id="2025-02-03-focus-notes",
        summary="Protected two focus blocks and captured deep work ideas.",
    )

    result = cli_runner.invoke(
        app,
        [
            "ops",
            "index",
            "search",
            "nonexistent topic",
            "--tags",
            "missing-tag",
        ],
    )
    assert result.exit_code == 0
    assert "No matches found." in result.stdout


def test_index_search_errors_when_index_missing(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    # workspace initialized but retrieval index not rebuilt
    result = cli_runner.invoke(app, ["ops", "index", "search", "anything"])
    assert result.exit_code != 0
    assert "Retrieval index not available" in (result.stderr or "")
