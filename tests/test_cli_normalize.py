"""Tests for the `aijournal normalize` Typer command."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import yaml

from aijournal.cli import app

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner

FROZEN_NOW = datetime(2025, 2, 3, 14, 5, 0, tzinfo=UTC)
EXPECTED_DATE = "2025-02-03"
EXPECTED_SLUG = "2025-02-03-sync-notes"


@pytest.fixture(autouse=True)
def freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    import aijournal.cli as cli_module

    monkeypatch.setattr(cli_module, "_now", lambda: FROZEN_NOW, raising=False)


def _write_markdown(path: Path, *, created_at: str = "2025-02-03T14:05:00Z") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
id: 2025-02-03-sync-notes
created_at: {created_at}
title: Sync Notes
tags: [team, planning]
---

# Monday Sync
Discussed roadmap and blockers.

## Decisions
Ship MVP this week.
""",
        encoding="utf-8",
    )


def _read_yaml(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_normalize_creates_yaml(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    entry_path = cli_workspace / "data" / "journal" / "2025" / "02" / "03" / f"{EXPECTED_SLUG}.md"
    _write_markdown(entry_path)

    result = cli_runner.invoke(app, ["ops", "pipeline", "normalize", str(entry_path)])

    assert result.exit_code == 0, result.output

    normalized_path = (
        cli_workspace / "data" / "normalized" / EXPECTED_DATE / f"{EXPECTED_SLUG}.yaml"
    )
    assert normalized_path.exists()

    data = _read_yaml(normalized_path)
    assert data["id"] == EXPECTED_SLUG
    assert data["created_at"] == "2025-02-03T14:05:00Z"
    assert data["source_path"].endswith(str(entry_path.relative_to(cli_workspace)))
    assert data["title"] == "Sync Notes"
    assert data["tags"] == ["team", "planning"]
    assert [section["heading"] for section in data["sections"]] == [
        "Monday Sync",
        "Decisions",
    ]
    assert str(normalized_path) in result.stdout


def test_normalize_is_idempotent(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    entry_path = cli_workspace / "data" / "journal" / "2025" / "02" / "03" / f"{EXPECTED_SLUG}.md"
    _write_markdown(entry_path)

    first = cli_runner.invoke(app, ["ops", "pipeline", "normalize", str(entry_path)])
    assert first.exit_code == 0

    normalized_path = (
        cli_workspace / "data" / "normalized" / EXPECTED_DATE / f"{EXPECTED_SLUG}.yaml"
    )
    before_mtime = normalized_path.stat().st_mtime

    second = cli_runner.invoke(app, ["ops", "pipeline", "normalize", str(entry_path)])
    assert second.exit_code == 0
    after_mtime = normalized_path.stat().st_mtime

    assert before_mtime == after_mtime, "File should not be rewritten on second run"


def test_normalize_converts_timezones_to_utc(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    entry_path = cli_workspace / "data" / "journal" / "2025" / "02" / "03" / f"{EXPECTED_SLUG}.md"
    _write_markdown(entry_path, created_at="2025-02-03T09:00:00-05:00")

    result = cli_runner.invoke(app, ["ops", "pipeline", "normalize", str(entry_path)])

    assert result.exit_code == 0, result.output

    normalized_path = (
        cli_workspace / "data" / "normalized" / EXPECTED_DATE / f"{EXPECTED_SLUG}.yaml"
    )
    data = _read_yaml(normalized_path)
    assert data["created_at"] == "2025-02-03T14:00:00Z"
