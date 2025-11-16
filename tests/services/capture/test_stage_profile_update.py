"""Tests for the unified profile update capture stage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Never

import typer

from aijournal.commands import profile_update as profile_update_module
from aijournal.common.app_config import AppConfig
from aijournal.services.capture import CaptureInput
from aijournal.services.capture import utils as capture_utils
from aijournal.services.capture.stages import stage4_profile_update

if TYPE_CHECKING:
    from pathlib import Path


def _make_inputs(apply_profile: str = "review") -> CaptureInput:
    return CaptureInput(source="stdin", text="Sample entry", apply_profile=apply_profile)


def _make_config() -> AppConfig:
    return AppConfig(
        paths={
            "data": "data",
            "derived": "derived",
            "profile": "profile",
            "prompts": "prompts",
        },
    )


def test_stage_profile_update_success(tmp_path: Path, monkeypatch) -> None:
    batch_path = tmp_path / "derived" / "pending" / "profile_updates" / "2025-10-27-batch.yaml"
    batch_path.parent.mkdir(parents=True, exist_ok=True)

    called: list[str] = []

    def fake_run(
        date: str,
        *,
        progress: bool,
        generate_preview: bool,
        workspace: Path | None = None,
        config: AppConfig | None = None,
    ) -> Path:
        del progress, generate_preview, workspace, config
        called.append(date)
        batch_path.write_text("batch", encoding="utf-8")
        return batch_path

    monkeypatch.setattr(profile_update_module, "run_profile_update", fake_run)

    outputs = stage4_profile_update.run_profile_update_stage(
        ["2025-10-27"],
        _make_inputs(),
        tmp_path,
        _make_config(),
    )

    assert called == ["2025-10-27"]
    assert outputs.result.ok is True
    assert outputs.result.changed is True
    assert outputs.new_batches == ["derived/pending/profile_updates/2025-10-27-batch.yaml"]
    assert outputs.review_result is None
    assert outputs.review_candidates == ["derived/pending/profile_updates/2025-10-27-batch.yaml"]


def test_stage_profile_update_handles_failure(tmp_path: Path, monkeypatch) -> None:
    def failing_run(*_args, **_kwargs) -> Never:
        raise typer.Exit(1)

    monkeypatch.setattr(profile_update_module, "run_profile_update", failing_run)

    outputs = stage4_profile_update.run_profile_update_stage(
        ["2025-10-27"],
        _make_inputs(),
        tmp_path,
        _make_config(),
    )

    assert outputs.result.ok is False
    assert outputs.result.changed is False
    assert outputs.result.warnings
    assert outputs.new_batches == []


def test_stage_profile_update_auto_apply(tmp_path: Path, monkeypatch) -> None:
    batch_path = tmp_path / "derived" / "pending" / "profile_updates" / "2025-10-27-batch.yaml"
    batch_path.parent.mkdir(parents=True, exist_ok=True)

    def fake_run(*_args, **_kwargs) -> Path:
        batch_path.write_text("batch", encoding="utf-8")
        return batch_path

    called_apply: list[Path] = []

    def fake_apply(root: Path, config: AppConfig, path: Path) -> bool:
        del root, config
        called_apply.append(path)
        return True

    monkeypatch.setattr(profile_update_module, "run_profile_update", fake_run)
    monkeypatch.setattr(capture_utils, "apply_profile_update_batch", fake_apply)

    outputs = stage4_profile_update.run_profile_update_stage(
        ["2025-10-27"],
        _make_inputs(apply_profile="auto"),
        tmp_path,
        _make_config(),
    )

    assert called_apply == [batch_path]
    assert outputs.review_result is not None
    assert outputs.review_result.ok is True
    assert outputs.applied_batches == ["derived/pending/profile_updates/2025-10-27-batch.yaml"]
