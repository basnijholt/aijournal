"""Tests for the `aijournal new` Typer command."""

from __future__ import annotations

from datetime import UTC, datetime, timezone
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app

runner = CliRunner()

FROZEN_NOW = datetime(2025, 1, 2, 9, 30, 15, tzinfo=UTC)
EXPECTED_DATE_PATH = Path("data/journal/2025/01/02")
EXPECTED_SLUG = "2025-01-02-kickoff-notes"


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
    import aijournal.cli as cli_module

    monkeypatch.setattr(cli_module, "_now", lambda: FROZEN_NOW, raising=False)


def _read_frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---\n")
    assert len(parts) >= 3, "Missing YAML frontmatter"
    frontmatter = parts[1]
    return yaml.safe_load(frontmatter)


def test_new_creates_journal_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["new", "Kickoff Notes"])

    assert result.exit_code == 0, result.stderr

    entry_path = tmp_path / EXPECTED_DATE_PATH / f"{EXPECTED_SLUG}.md"
    assert entry_path.exists()
    frontmatter = _read_frontmatter(entry_path)

    assert frontmatter["id"] == EXPECTED_SLUG
    assert frontmatter["created_at"] == "2025-01-02T09:30:15Z"
    assert frontmatter["title"] == "Kickoff Notes"
    assert frontmatter["tags"] == []
    assert str(entry_path) in result.stdout


def test_new_accepts_tags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        [
            "new",
            "Weekly Review",
            "--tags",
            "reflection",
            "--tags",
            "family",
        ],
    )

    assert result.exit_code == 0

    entry_path = tmp_path / EXPECTED_DATE_PATH / "2025-01-02-weekly-review.md"
    tags: list[str] = _read_frontmatter(entry_path)["tags"]
    assert tags == ["reflection", "family"]


def test_new_refuses_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    first = runner.invoke(app, ["new", "Kickoff Notes"])
    assert first.exit_code == 0

    second = runner.invoke(app, ["new", "Kickoff Notes"])
    assert second.exit_code != 0
    assert "exists" in second.stdout.lower()


def test_new_prints_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["new", "Kickoff Notes"])

    entry_path = tmp_path / EXPECTED_DATE_PATH / f"{EXPECTED_SLUG}.md"
    assert str(entry_path) in result.stdout
