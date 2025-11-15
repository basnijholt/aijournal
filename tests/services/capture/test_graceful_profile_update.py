"""Tests for the graceful profile update wrapper."""

from __future__ import annotations

from pathlib import Path

import typer

from aijournal.common.app_config import AppConfig
from aijournal.services.capture.graceful import graceful_profile_update


def test_graceful_profile_update_success(tmp_path: Path, monkeypatch) -> None:
    batch_path = tmp_path / "derived" / "pending" / "profile_updates" / "test.yaml"
    batch_path.parent.mkdir(parents=True, exist_ok=True)

    def fake_run(
        date: str,
        *,
        progress: bool,
        build_claim_preview,
        workspace: Path | None = None,
        config: AppConfig | None = None,
    ) -> Path:
        del date, progress, build_claim_preview, workspace, config
        batch_path.write_text("batch", encoding="utf-8")
        return batch_path

    monkeypatch.setattr("aijournal.commands.profile_update.run_profile_update", fake_run)

    path, error = graceful_profile_update(
        "2025-10-27",
        progress=False,
        build_claim_preview=lambda *_args, **_kwargs: None,
        workspace=tmp_path,
        config=AppConfig(),
    )

    assert error is None
    assert path == batch_path


def test_graceful_profile_update_failure(tmp_path: Path, monkeypatch) -> None:
    def failing_run(*_args, **_kwargs):
        raise typer.Exit(1)

    monkeypatch.setattr("aijournal.commands.profile_update.run_profile_update", failing_run)

    path, error = graceful_profile_update(
        "2025-10-27",
        progress=False,
        build_claim_preview=lambda *_args, **_kwargs: None,
        workspace=tmp_path,
        config=AppConfig(),
    )

    assert path is None
    assert error is not None
