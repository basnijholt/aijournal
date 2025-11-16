"""Tests for workspace path resolution and validation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from aijournal.cli import (
    CLISettings,
    _get_workspace,
    _resolve_workspace_option,
    app,
)
from aijournal.services.capture import CAPTURE_MAX_STAGE

if TYPE_CHECKING:
    from typer.testing import CliRunner


def _set_cli_workspace(monkeypatch: pytest.MonkeyPatch, workspace: Path | None) -> None:
    def fake_settings() -> CLISettings:
        return CLISettings(workspace=workspace)

    monkeypatch.setattr("aijournal.cli._cli_settings", fake_settings)


def test_resolve_workspace_option_expands_tilde(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))

    resolved = _resolve_workspace_option(Path("~/workspace"))
    assert resolved == workspace.resolve()


def test_resolve_workspace_option_resolves_relative_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    workspace = sub / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text("model: test\n")
    monkeypatch.chdir(sub)

    resolved = _resolve_workspace_option(Path("./workspace"))
    assert resolved == workspace.resolve()


def test_get_workspace_uses_cli_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text("model: test\n")
    _set_cli_workspace(monkeypatch, workspace.resolve())

    assert _get_workspace() == workspace.resolve()


def test_get_workspace_defaults_to_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text("model: test\n")
    _set_cli_workspace(monkeypatch, None)
    monkeypatch.chdir(workspace)

    assert _get_workspace() == workspace


def test_get_workspace_fails_on_missing_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing"
    _set_cli_workspace(monkeypatch, missing)

    with pytest.raises(RuntimeError, match="Workspace directory does not exist"):
        _get_workspace()


def test_get_workspace_fails_on_file_instead_of_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_workspace = tmp_path / "workspace_file"
    fake_workspace.write_text("not a directory")
    _set_cli_workspace(monkeypatch, fake_workspace)

    with pytest.raises(RuntimeError, match="Workspace path is not a directory"):
        _get_workspace()


def test_get_workspace_fails_on_missing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _set_cli_workspace(monkeypatch, workspace)

    with pytest.raises(RuntimeError, match=r"Missing config.yaml"):
        _get_workspace()


def test_status_command_respects_workspace_option(
    tmp_path: Path,
    cli_runner: CliRunner,
) -> None:
    workspace = tmp_path / "custom_workspace"
    workspace.mkdir()
    (workspace / "config.yaml").write_text("model: test\n")
    (workspace / "profile").mkdir()
    (workspace / "profile" / "self_profile.yaml").write_text("traits: {}\n")
    (workspace / "profile" / "claims.yaml").write_text("claims: []\n")
    (workspace / "derived").mkdir()
    (workspace / "derived" / "persona").mkdir()

    result = cli_runner.invoke(
        app,
        ["--path", str(workspace), "status"],
        env={"AIJOURNAL_FAKE_OLLAMA": "1"},
    )
    assert result.exit_code in (0, 1), f"Unexpected exit code: {result.exit_code}\n{result.output}"


def test_capture_uses_workspace_option(
    tmp_path: Path,
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "journal"
    result = cli_runner.invoke(app, ["--path", str(workspace), "init"])
    assert result.exit_code == 0, result.stdout

    captured: dict[str, Path | None] = {"root": None}

    class DummyResult:
        errors: list[str] = []
        warnings: list[str] = []
        entries: list[object] = []
        stages_completed: list[int] = []
        min_stage: int = 0
        max_stage: int = CAPTURE_MAX_STAGE
        run_id: str = "capture-test"

    def fake_run_capture(
        inputs: object,
        *,
        root: Path | None = None,
        **kwargs: object,
    ) -> DummyResult:
        captured["root"] = root
        return DummyResult()

    monkeypatch.setattr("aijournal.cli.run_capture", fake_run_capture)

    capture = cli_runner.invoke(
        app,
        ["--path", str(workspace), "capture", "--text", "checkpoint"],
        env={"AIJOURNAL_FAKE_OLLAMA": "1"},
    )
    assert capture.exit_code == 0, capture.stdout
    assert captured["root"] == workspace
