"""Tests for the `aijournal init` Typer command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aijournal.cli import app
from aijournal.utils.paths import AUTHORITATIVE_DIRS, DERIVED_DIRS

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    import pytest
    from typer.testing import CliRunner


ALL_LAYOUT_DIRS = set(AUTHORITATIVE_DIRS) | set(DERIVED_DIRS)

SEED_FILES = {
    "config.yaml",
    "profile/self_profile.yaml",
    "profile/claims.yaml",
}


def _assert_paths_exist(base: Path, relative_paths: Iterable[str]) -> None:
    for rel in relative_paths:
        path = base / rel
        assert path.exists(), f"Expected path missing: {rel}"


def _assert_gitkeep_markers(base: Path, relative_dirs: Iterable[str]) -> None:
    for rel in relative_dirs:
        marker = (base / rel / ".gitkeep").resolve()
        assert marker.exists(), f"Missing .gitkeep for {rel}"
        assert marker.is_file(), f".gitkeep for {rel} is not a file"


def _collect_relative(base: Path, *, files: bool) -> set[str]:
    items: set[str] = set()
    for candidate in base.rglob("*"):
        if candidate.is_file() is files:
            items.add(candidate.relative_to(base).as_posix())
    return items


def test_init_creates_expected_structure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_runner: CliRunner,
) -> None:
    """First run should create every required directory and seed file."""
    monkeypatch.chdir(tmp_path)
    result = cli_runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output

    _assert_paths_exist(tmp_path, ALL_LAYOUT_DIRS)
    _assert_paths_exist(tmp_path, SEED_FILES)
    _assert_gitkeep_markers(tmp_path, ALL_LAYOUT_DIRS)

    created_dirs = _collect_relative(tmp_path, files=False)
    created_files = _collect_relative(tmp_path, files=True)

    assert created_dirs >= ALL_LAYOUT_DIRS
    assert created_files >= SEED_FILES


def test_init_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_runner: CliRunner,
) -> None:
    """Running the command twice should not overwrite files."""
    monkeypatch.chdir(tmp_path)

    first = cli_runner.invoke(app, ["init"])
    assert first.exit_code == 0

    before_stats = {rel: (tmp_path / rel).stat().st_mtime for rel in SEED_FILES}

    second = cli_runner.invoke(app, ["init"])
    assert second.exit_code == 0

    after_stats = {rel: (tmp_path / rel).stat().st_mtime for rel in SEED_FILES}
    assert before_stats == after_stats, "Seed files were unexpectedly modified"


def test_init_respects_path_argument(
    tmp_path: Path,
    cli_runner: CliRunner,
) -> None:
    """`--path` should bootstrap a custom directory instead of cwd."""
    target = tmp_path / "custom"
    result = cli_runner.invoke(app, ["--path", str(target), "init"])

    assert result.exit_code == 0, result.output
    _assert_paths_exist(target, ALL_LAYOUT_DIRS)
    _assert_paths_exist(target, SEED_FILES)
    _assert_gitkeep_markers(target, ALL_LAYOUT_DIRS)


def test_init_prints_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_runner: CliRunner,
) -> None:
    """Command output should summarize created vs existing paths."""
    monkeypatch.chdir(tmp_path)

    first = cli_runner.invoke(app, ["init"])
    assert first.exit_code == 0
    summary = first.stdout.lower()
    assert "created" in summary
    assert "directories" in summary
    assert "files" in summary

    second = cli_runner.invoke(app, ["init"])
    assert second.exit_code == 0
    assert "already" in second.stdout.lower()
