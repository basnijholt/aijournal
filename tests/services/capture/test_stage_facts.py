"""Tests for stage3_facts graceful error handling."""

from __future__ import annotations

from pathlib import Path

import typer

from aijournal.common.app_config import AppConfig
from aijournal.services.capture import CaptureInput
from aijournal.services.capture.stages import stage3_facts


def _make_inputs() -> CaptureInput:
    return CaptureInput(source="stdin", text="Sample entry")


def _make_config() -> AppConfig:
    return AppConfig(
        paths={
            "data": "data",
            "derived": "derived",
            "profile": "profile",
            "prompts": "prompts",
        }
    )


def test_stage3_facts_success(tmp_path: Path, monkeypatch) -> None:
    facts_path = tmp_path / "derived" / "microfacts" / "2025-10-27.yaml"
    facts_path.parent.mkdir(parents=True, exist_ok=True)

    called: list[str] = []

    def fake_run(
        date: str,
        *,
        timeout: float,
        retries: int,
        progress: bool,
        claim_models,
        build_claim_preview,
        workspace: Path | None = None,
    ) -> tuple[None, Path]:
        called.append(date)
        facts_path.write_text("facts", encoding="utf-8")
        return None, facts_path

    def fake_load_profile(*args, **kwargs):
        return None, []  # profile, claims

    monkeypatch.setattr("aijournal.commands.facts.run_facts", fake_run)
    monkeypatch.setattr("aijournal.commands.profile.load_profile_components", fake_load_profile)

    outputs = stage3_facts.run_facts_stage_3(
        ["2025-10-27"], _make_inputs(), tmp_path, _make_config()
    )

    assert called == ["2025-10-27"]
    assert outputs.result.ok is True
    assert outputs.result.changed is True
    assert outputs.paths == ["derived/microfacts/2025-10-27.yaml"]


def test_stage3_facts_handles_failure(tmp_path: Path, monkeypatch) -> None:
    def failing_run(*args, **kwargs):
        raise typer.Exit(1)

    def fake_load_profile(*args, **kwargs):
        return None, []

    monkeypatch.setattr("aijournal.commands.facts.run_facts", failing_run)
    monkeypatch.setattr("aijournal.commands.profile.load_profile_components", fake_load_profile)

    outputs = stage3_facts.run_facts_stage_3(
        ["2025-10-27"], _make_inputs(), tmp_path, _make_config()
    )

    assert outputs.result.ok is False
    assert outputs.result.changed is False
    assert outputs.result.warnings
    assert outputs.paths == []
