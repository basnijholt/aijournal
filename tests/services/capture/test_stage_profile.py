"""Tests for stage4_profile graceful error handling."""

from __future__ import annotations

from pathlib import Path

import typer

from aijournal.common.app_config import AppConfig
from aijournal.services.capture import CaptureInput
from aijournal.services.capture.stages import stage4_profile


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


def test_stage4_profile_suggest_success(tmp_path: Path, monkeypatch) -> None:
    suggestions_path = tmp_path / "derived" / "profile_proposals" / "2025-10-27.yaml"
    suggestions_path.parent.mkdir(parents=True, exist_ok=True)

    called_suggest: list[str] = []

    def fake_suggest(
        date: str,
        *,
        timeout: float,
        retries: int,
        progress: bool,
        workspace: Path | None = None,
    ) -> Path:
        called_suggest.append(date)
        suggestions_path.write_text("suggestions", encoding="utf-8")
        return suggestions_path

    monkeypatch.setattr("aijournal.commands.profile.run_profile_suggest", fake_suggest)

    outputs = stage4_profile.run_profile_stage_4(
        ["2025-10-27"], _make_inputs(), tmp_path, _make_config()
    )

    assert called_suggest == ["2025-10-27"]
    assert outputs.suggest_result.ok is True
    assert outputs.suggest_result.changed is True
    assert outputs.suggestion_paths == ["derived/profile_proposals/2025-10-27.yaml"]
    assert outputs.apply_result is None


def test_stage4_profile_suggest_handles_failure(tmp_path: Path, monkeypatch) -> None:
    def failing_suggest(*args, **kwargs):
        raise typer.Exit(1)

    monkeypatch.setattr("aijournal.commands.profile.run_profile_suggest", failing_suggest)

    outputs = stage4_profile.run_profile_stage_4(
        ["2025-10-27"], _make_inputs(), tmp_path, _make_config()
    )

    assert outputs.suggest_result.ok is False
    assert outputs.suggest_result.changed is False
    assert outputs.suggest_result.warnings
    assert outputs.suggestion_paths == []


def test_stage4_profile_auto_apply_success(tmp_path: Path, monkeypatch) -> None:
    suggestions_path = tmp_path / "derived" / "profile_proposals" / "2025-10-27.yaml"
    suggestions_path.parent.mkdir(parents=True, exist_ok=True)

    called_suggest: list[str] = []
    called_apply: list[str] = []

    def fake_suggest(
        date: str,
        *,
        timeout: float,
        retries: int,
        progress: bool,
        workspace: Path | None = None,
    ) -> Path:
        called_suggest.append(date)
        suggestions_path.write_text("suggestions", encoding="utf-8")
        return suggestions_path

    def fake_apply(
        date: str,
        *,
        suggestions_path: Path,
        auto_confirm: bool,
        workspace: Path | None = None,
    ) -> str:
        called_apply.append(date)
        return "Applied"

    monkeypatch.setattr("aijournal.commands.profile.run_profile_suggest", fake_suggest)
    monkeypatch.setattr("aijournal.commands.profile.run_profile_apply", fake_apply)

    outputs = stage4_profile.run_profile_stage_4(
        ["2025-10-27"], _make_inputs(apply_profile="auto"), tmp_path, _make_config()
    )

    assert called_suggest == ["2025-10-27"]
    assert called_apply == ["2025-10-27"]
    assert outputs.suggest_result.ok is True
    assert outputs.apply_result is not None
    assert outputs.apply_result.ok is True
    assert outputs.applied_count == 1


def test_stage4_profile_apply_handles_failure(tmp_path: Path, monkeypatch) -> None:
    suggestions_path = tmp_path / "derived" / "profile_proposals" / "2025-10-27.yaml"
    suggestions_path.parent.mkdir(parents=True, exist_ok=True)

    def fake_suggest(*args, **kwargs) -> Path:
        suggestions_path.write_text("suggestions", encoding="utf-8")
        return suggestions_path

    def failing_apply(*args, **kwargs):
        raise typer.Exit(1)

    monkeypatch.setattr("aijournal.commands.profile.run_profile_suggest", fake_suggest)
    monkeypatch.setattr("aijournal.commands.profile.run_profile_apply", failing_apply)

    outputs = stage4_profile.run_profile_stage_4(
        ["2025-10-27"], _make_inputs(apply_profile="auto"), tmp_path, _make_config()
    )

    assert outputs.suggest_result.ok is True
    assert outputs.apply_result is not None
    assert outputs.apply_result.ok is False
    assert outputs.apply_result.warnings
    assert outputs.applied_count == 0
