"""Tests for the `aijournal new` Typer command."""

from __future__ import annotations

from datetime import UTC, datetime, timezone
from typing import TYPE_CHECKING

import pytest
import yaml

from aijournal.cli import app

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner

FROZEN_NOW = datetime(2025, 1, 2, 9, 30, 15, tzinfo=UTC)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz: timezone | None = None):  # type: ignore[override]
        if tz is None:
            return FROZEN_NOW.replace(tzinfo=None)
        return FROZEN_NOW.astimezone(tz)

    @classmethod
    def utcnow(cls):  # type: ignore[override]
        return FROZEN_NOW


@pytest.fixture(autouse=True)
def freeze_datetime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aijournal.utils.time.now", lambda: FROZEN_NOW, raising=False)


def _read_frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---\n")
    assert len(parts) >= 3, "Missing YAML frontmatter"
    frontmatter = parts[1]
    return yaml.safe_load(frontmatter)


def _read_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---\n", 2)
    assert len(parts) == 3, "Missing body"
    return parts[2].strip()


def _collect_journal_files(workspace: Path) -> set[Path]:
    journal_root = workspace / "data" / "journal"
    if not journal_root.exists():
        return set()
    return set(journal_root.rglob("*.md"))


def test_new_creates_journal_entry(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    before = _collect_journal_files(cli_workspace)
    result = cli_runner.invoke(app, ["ops", "dev", "new", "Kickoff Notes"])

    assert result.exit_code == 0, result.stderr

    after = _collect_journal_files(cli_workspace)
    created = after - before
    assert len(created) == 1
    entry_path = created.pop()
    frontmatter = _read_frontmatter(entry_path)

    assert frontmatter["id"] == entry_path.stem
    assert frontmatter["title"] == "Kickoff Notes"
    assert frontmatter["tags"] == []
    assert str(entry_path) in result.stdout


def test_new_accepts_tags(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    before = _collect_journal_files(cli_workspace)

    result = cli_runner.invoke(
        app,
        [
            "ops",
            "dev",
            "new",
            "Weekly Review",
            "--tags",
            "reflection",
            "--tags",
            "family",
        ],
    )

    assert result.exit_code == 0

    created = _collect_journal_files(cli_workspace) - before
    assert len(created) == 1
    entry_path = created.pop()
    tags: list[str] = _read_frontmatter(entry_path)["tags"]
    assert tags == ["reflection", "family"]


def test_new_refuses_overwrite(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    first = cli_runner.invoke(app, ["ops", "dev", "new", "Kickoff Notes"])
    assert first.exit_code == 0

    second = cli_runner.invoke(app, ["ops", "dev", "new", "Kickoff Notes"])
    assert second.exit_code != 0
    assert "exists" in second.stdout.lower()


def test_new_prints_path(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    before = _collect_journal_files(cli_workspace)
    result = cli_runner.invoke(app, ["ops", "dev", "new", "Kickoff Notes"])

    created = _collect_journal_files(cli_workspace) - before
    assert len(created) == 1
    entry_path = created.pop()
    assert str(entry_path) in result.stdout


def test_new_requires_title_without_fake(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    result = cli_runner.invoke(app, ["ops", "dev", "new"])

    assert result.exit_code != 0
    assert "Title is required" in result.stderr


def test_new_seed_requires_fake(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    result = cli_runner.invoke(app, ["ops", "dev", "new", "Kickoff", "--seed", "7"])

    assert result.exit_code != 0
    assert "only valid" in result.stderr


def test_new_fake_generates_entries(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    before = _collect_journal_files(cli_workspace)

    result = cli_runner.invoke(
        app,
        ["ops", "dev", "new", "--fake", "2", "--seed", "7"],
    )

    assert result.exit_code == 0, result.stderr

    created = sorted(_collect_journal_files(cli_workspace) - before)
    assert len(created) == 2

    first_meta = _read_frontmatter(created[0])
    second_meta = _read_frontmatter(created[1])

    assert first_meta["projects"]
    assert second_meta["projects"]
    assert first_meta["tags"]
    assert second_meta["tags"]

    body = _read_body(created[1])
    assert body
    assert "Generated 2 fake entries" in result.stdout


def test_new_fake_disallows_title(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    result = cli_runner.invoke(app, ["ops", "dev", "new", "Kickoff", "--fake", "1"])

    assert result.exit_code != 0
    assert "Provide either a title" in result.stderr
