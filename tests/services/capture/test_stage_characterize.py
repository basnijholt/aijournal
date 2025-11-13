"""Tests for stage5_characterize graceful error handling."""

from __future__ import annotations

from pathlib import Path

import typer

from aijournal.common.app_config import AppConfig
from aijournal.services.capture import CaptureInput
from aijournal.services.capture.stages import stage5_characterize


def _make_inputs(apply_profile: str = "review") -> CaptureInput:
    return CaptureInput(source="stdin", text="Sample entry", apply_profile=apply_profile)


def _make_config() -> AppConfig:
    return AppConfig(
        paths={
            "data": "data",
            "derived": "derived",
            "profile": "profile",
            "prompts": "prompts",
        }
    )


def test_stage5_characterize_success(tmp_path: Path, monkeypatch) -> None:
    batch_path = tmp_path / "derived" / "pending" / "profile_updates" / "batch_2025-10-27.yaml"
    batch_path.parent.mkdir(parents=True, exist_ok=True)

    called: list[str] = []

    def fake_run(
        date: str,
        *,
        timeout: float,
        retries: int,
        progress: bool,
        build_claim_preview,
        workspace: Path | None = None,
    ) -> Path:
        called.append(date)
        batch_path.write_text("batch", encoding="utf-8")
        return batch_path

    def fake_pending(*args, **kwargs):
        return set()

    monkeypatch.setattr("aijournal.commands.characterize.run_characterize", fake_run)
    monkeypatch.setattr("aijournal.services.capture.utils.pending_batches", fake_pending)

    outputs = stage5_characterize.run_characterize_stage_5(
        ["2025-10-27"], _make_inputs(), tmp_path, _make_config()
    )

    assert called == ["2025-10-27"]
    assert outputs.result.ok is True
    assert outputs.result.changed is True
    assert len(outputs.new_batches) == 1


def test_stage5_characterize_handles_failure(tmp_path: Path, monkeypatch) -> None:
    def failing_run(*args, **kwargs):
        raise typer.Exit(1)

    def fake_pending(*args, **kwargs):
        return set()

    monkeypatch.setattr("aijournal.commands.characterize.run_characterize", failing_run)
    monkeypatch.setattr("aijournal.services.capture.utils.pending_batches", fake_pending)

    outputs = stage5_characterize.run_characterize_stage_5(
        ["2025-10-27"], _make_inputs(), tmp_path, _make_config()
    )

    assert outputs.result.ok is False
    assert outputs.result.changed is False
    assert outputs.result.warnings
    assert outputs.new_batches == []


def test_stage5_characterize_auto_apply(tmp_path: Path, monkeypatch) -> None:
    batch_path = tmp_path / "derived" / "pending" / "profile_updates" / "batch_2025-10-27.yaml"
    batch_path.parent.mkdir(parents=True, exist_ok=True)

    called_characterize: list[str] = []
    called_apply: list[Path] = []

    def fake_run(
        date: str,
        *,
        timeout: float,
        retries: int,
        progress: bool,
        build_claim_preview,
        workspace: Path | None = None,
    ) -> Path:
        called_characterize.append(date)
        batch_path.write_text("batch", encoding="utf-8")
        return batch_path

    def fake_pending(*args, **kwargs):
        return set()

    def fake_apply(root: Path, config: AppConfig, path: Path) -> bool:
        called_apply.append(path)
        return True

    monkeypatch.setattr("aijournal.commands.characterize.run_characterize", fake_run)
    monkeypatch.setattr("aijournal.services.capture.utils.pending_batches", fake_pending)
    monkeypatch.setattr("aijournal.services.capture.utils.apply_profile_update_batch", fake_apply)

    outputs = stage5_characterize.run_characterize_stage_5(
        ["2025-10-27"], _make_inputs(apply_profile="auto"), tmp_path, _make_config()
    )

    assert called_characterize == ["2025-10-27"]
    assert len(called_apply) == 1
    assert outputs.result.ok is True
    assert outputs.review_result is not None
    assert outputs.review_result.ok is True
    assert len(outputs.applied_batches) == 1
