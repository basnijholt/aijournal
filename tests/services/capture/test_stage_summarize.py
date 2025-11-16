from __future__ import annotations

from typing import TYPE_CHECKING, Never

import typer

from aijournal.common.app_config import AppConfig
from aijournal.services.capture import CaptureInput
from aijournal.services.capture.stages import stage2_summarize

if TYPE_CHECKING:
    from pathlib import Path


def _make_inputs() -> CaptureInput:
    return CaptureInput(source="stdin", text="Sample entry")


def test_stage2_summarize_success(tmp_path: Path, monkeypatch) -> None:
    summary_path = tmp_path / "derived" / "summaries" / "2025-10-27.yaml"
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    called: list[str] = []

    def fake_run(
        date: str,
        *,
        progress: bool,
        workspace: Path | None = None,
        config: AppConfig | None = None,
    ) -> Path:
        called.append(date)
        summary_path.write_text("summary", encoding="utf-8")
        return summary_path

    monkeypatch.setattr("aijournal.commands.summarize.run_summarize", fake_run)

    outputs = stage2_summarize.run_summarize_stage_2(
        ["2025-10-27"],
        _make_inputs(),
        tmp_path,
        AppConfig(),
    )

    assert called == ["2025-10-27"]
    assert outputs.result.ok is True
    assert outputs.result.changed is True
    assert outputs.paths == ["derived/summaries/2025-10-27.yaml"]


def test_stage2_summarize_handles_failure(tmp_path: Path, monkeypatch) -> None:
    def failing_run(*args, **kwargs) -> Never:
        raise typer.Exit(1)

    monkeypatch.setattr("aijournal.commands.summarize.run_summarize", failing_run)

    outputs = stage2_summarize.run_summarize_stage_2(
        ["2025-10-27"],
        _make_inputs(),
        tmp_path,
        AppConfig(),
    )

    assert outputs.result.ok is False
    assert outputs.result.changed is False
    assert outputs.result.warnings
    assert outputs.paths == []
