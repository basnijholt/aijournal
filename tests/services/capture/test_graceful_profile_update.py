"""Tests for the graceful profile update wrapper."""

from __future__ import annotations

from typing import TYPE_CHECKING, Never

import typer

from aijournal.common.app_config import AppConfig
from aijournal.services.capture.graceful import graceful_profile_update

if TYPE_CHECKING:
    from pathlib import Path


def test_graceful_profile_update_success(tmp_path: Path, monkeypatch) -> None:
    batch_path = tmp_path / "derived" / "pending" / "profile_updates" / "test.yaml"
    batch_path.parent.mkdir(parents=True, exist_ok=True)

    def fake_run(
        date: str,
        *,
        progress: bool,
        generate_preview: bool,
        workspace: Path | None = None,
        config: AppConfig | None = None,
    ) -> Path:
        del date, progress, generate_preview, workspace, config
        batch_path.write_text("batch", encoding="utf-8")
        return batch_path

    monkeypatch.setattr("aijournal.commands.profile_update.run_profile_update", fake_run)

    path, error = graceful_profile_update(
        "2025-10-27",
        progress=False,
        generate_preview=False,
        workspace=tmp_path,
        config=AppConfig(),
    )

    assert error is None
    assert path == batch_path


def test_graceful_profile_update_failure(tmp_path: Path, monkeypatch) -> None:
    def failing_run(*_args, **_kwargs) -> Never:
        raise typer.Exit(1)

    monkeypatch.setattr("aijournal.commands.profile_update.run_profile_update", failing_run)

    path, error = graceful_profile_update(
        "2025-10-27",
        progress=False,
        generate_preview=False,
        workspace=tmp_path,
        config=AppConfig(),
    )

    assert path is None
    assert error is not None
