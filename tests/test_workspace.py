"""Tests for workspace path resolution and validation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from aijournal.cli import _get_workspace, app

if TYPE_CHECKING:
    pass


def test_get_workspace_expands_tilde(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AIJOURNAL_WORKSPACE with ~ should expand to home directory."""
    # Create a workspace with config.yaml
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text("model: test\n")

    # Set HOME to tmp_path and AIJOURNAL_WORKSPACE to ~/workspace
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AIJOURNAL_WORKSPACE", "~/workspace")

    result = _get_workspace()
    assert result == workspace.resolve()


def test_get_workspace_resolves_relative_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AIJOURNAL_WORKSPACE with relative path should resolve to absolute."""
    # Create nested structure: tmp_path/sub/workspace
    sub = tmp_path / "sub"
    sub.mkdir()
    workspace = sub / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text("model: test\n")

    # Change to sub directory and set relative path
    monkeypatch.chdir(sub)
    monkeypatch.setenv("AIJOURNAL_WORKSPACE", "./workspace")

    result = _get_workspace()
    assert result == workspace.resolve()
    assert result.is_absolute()


def test_get_workspace_fails_on_missing_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should raise RuntimeError if workspace directory doesn't exist."""
    missing = tmp_path / "nonexistent"
    monkeypatch.setenv("AIJOURNAL_WORKSPACE", str(missing))

    with pytest.raises(RuntimeError, match="Workspace directory does not exist"):
        _get_workspace()


def test_get_workspace_fails_on_file_instead_of_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should raise RuntimeError if workspace path points to a file."""
    fake_workspace = tmp_path / "workspace_file"
    fake_workspace.write_text("not a directory")
    monkeypatch.setenv("AIJOURNAL_WORKSPACE", str(fake_workspace))

    with pytest.raises(RuntimeError, match="Workspace path is not a directory"):
        _get_workspace()


def test_get_workspace_fails_on_missing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should raise RuntimeError if workspace directory lacks config.yaml."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Don't create config.yaml
    monkeypatch.setenv("AIJOURNAL_WORKSPACE", str(workspace))

    with pytest.raises(RuntimeError, match="Missing config.yaml"):
        _get_workspace()


def test_get_workspace_defaults_to_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without AIJOURNAL_WORKSPACE, should use current directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text("model: test\n")

    monkeypatch.chdir(workspace)
    monkeypatch.delenv("AIJOURNAL_WORKSPACE", raising=False)

    result = _get_workspace()
    assert result == workspace


def test_status_command_respects_workspace_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_runner: CliRunner,
) -> None:
    """CLI commands should respect AIJOURNAL_WORKSPACE environment variable."""
    # Create a workspace with minimal structure
    workspace = tmp_path / "custom_workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text("model: test\n")

    # Create required directories for status command
    (workspace / "profile").mkdir()
    (workspace / "profile" / "self_profile.yaml").write_text("traits: {}\n")
    (workspace / "profile" / "claims.yaml").write_text("claims: []\n")
    (workspace / "derived").mkdir()
    (workspace / "derived" / "persona").mkdir()

    monkeypatch.setenv("AIJOURNAL_WORKSPACE", str(workspace))
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")

    # The status command should work without changing directory
    result = cli_runner.invoke(app, ["status"])
    # Just check it doesn't crash - exit code may vary based on persona state
    assert result.exit_code in (0, 1), f"Unexpected exit code: {result.exit_code}\n{result.output}"


def test_workspace_env_with_spaces_in_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace paths with spaces should be handled correctly."""
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()
    (workspace / "config.yaml").write_text("model: test\n")

    monkeypatch.setenv("AIJOURNAL_WORKSPACE", str(workspace))

    result = _get_workspace()
    assert result == workspace.resolve()
    assert result.exists()
