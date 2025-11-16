"""Tests for `aijournal summarize` using fake Ollama outputs."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

import aijournal.services.ollama
from aijournal.cli import app
from aijournal.commands import summarize as summarize_commands
from aijournal.common import config_loader
from aijournal.common.app_config import AppConfig
from aijournal.common.constants import DEFAULT_LLM_RETRIES
from aijournal.common.meta import LLMResult
from aijournal.domain.facts import DailySummary
from aijournal.domain.journal import NormalizedEntry
from aijournal.io.yaml_io import dump_yaml
from aijournal.models.authoritative import JournalSection
from aijournal.services.ollama import LLMResponseError, OllamaConfig

if TYPE_CHECKING:
    from typer.testing import CliRunner

DATE = "2025-02-03"
ENTRY_ID = "2025-02-03-sync-notes"


def _write_normalized(workspace: Path) -> Path:
    normalized = workspace / "data" / "normalized" / DATE / f"{ENTRY_ID}.yaml"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    normalized.write_text(
        dump_yaml(
            {
                "id": ENTRY_ID,
                "created_at": "2025-02-03T14:05:00Z",
                "source_path": f"data/journal/2025/02/03/{ENTRY_ID}.md",
                "title": "Sync Notes",
                "tags": ["team"],
                "sections": [
                    {"heading": "Monday Sync", "level": 1},
                    {"heading": "Decisions", "level": 2},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return normalized


def _read_yaml(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_summarize_generates_summary(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _write_normalized(cli_workspace)

    result = cli_runner.invoke(app, ["ops", "pipeline", "summarize", "--date", DATE])

    assert result.exit_code == 0, result.stdout

    summary_path = cli_workspace / "derived" / "summaries" / f"{DATE}.yaml"
    assert summary_path.exists()

    artifact = _read_yaml(summary_path)
    assert artifact.get("kind") == "summaries.daily"
    meta = artifact.get("meta", {})
    assert meta.get("created_at")
    assert meta.get("model") == "fake-ollama"
    assert meta.get("prompt_path") == "prompts/summarize_day.md"
    data = artifact.get("data", {})
    assert data.get("day") == DATE
    assert isinstance(data.get("highlights"), list)
    assert isinstance(data.get("todo_candidates"), list)
    assert "meta" not in data
    assert str(summary_path) in result.stdout


def test_summarize_is_idempotent(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _write_normalized(cli_workspace)

    first = cli_runner.invoke(app, ["ops", "pipeline", "summarize", "--date", DATE])
    assert first.exit_code == 0

    summary_path = cli_workspace / "derived" / "summaries" / f"{DATE}.yaml"
    before = summary_path.stat().st_mtime

    second = cli_runner.invoke(app, ["ops", "pipeline", "summarize", "--date", DATE])
    assert second.exit_code == 0
    after = summary_path.stat().st_mtime

    assert before == after


def test_summarize_progress_flag(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _write_normalized(cli_workspace)

    result = cli_runner.invoke(
        app,
        ["ops", "pipeline", "summarize", "--date", DATE, "--progress"],
    )

    assert result.exit_code == 0, result.stdout
    assert "Summarizing entries for" in result.stdout
    assert "[1/1]" in result.stdout


def test_summarize_rejects_zero_timeout(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _write_normalized(cli_workspace)

    result = cli_runner.invoke(
        app,
        ["ops", "pipeline", "summarize", "--date", DATE, "--timeout", "0"],
    )

    assert result.exit_code != 0
    assert "--timeout must be positive" in result.stdout


def test_summarize_structured_success(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = NormalizedEntry(
        id="entry-1",
        created_at=f"{DATE}T09:00:00Z",
        source_path="data/journal/2025/02/03/entry-1.md",
        title="Sync Notes",
        tags=["team"],
        sections=[JournalSection(heading="Updates", level=1)],
        summary=None,
    )

    fake_response = DailySummary(
        day=DATE,
        bullets=["bullet"],
        highlights=["highlight"],
        todo_candidates=["todo"],
    )

    def fake_invoke(*_args, **_kwargs) -> DailySummary:
        return fake_response

    monkeypatch.setattr(config_loader, "use_fake_llm", lambda: False)
    monkeypatch.setattr(summarize_commands, "invoke_structured_llm", fake_invoke)

    summary = summarize_commands._summarize_day_payload(
        [entry],
        DATE,
        AppConfig(),
        workspace=Path(),
    )

    assert summary.day == DATE
    assert summary.bullets
    assert summary.highlights
    assert summary.todo_candidates


def test_summarize_structured_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = NormalizedEntry(
        id="entry-1",
        created_at=f"{DATE}T09:00:00Z",
        source_path="data/journal/2025/02/03/entry-1.md",
        title="Sync Notes",
        tags=["team"],
        sections=[JournalSection(heading="Updates", level=1)],
        summary=None,
    )

    def fake_invoke(*_args, **_kwargs) -> DailySummary:
        msg = "bad schema"
        raise LLMResponseError(msg)

    monkeypatch.setattr(config_loader, "use_fake_llm", lambda: False)
    monkeypatch.setattr(summarize_commands, "invoke_structured_llm", fake_invoke)

    with pytest.raises(LLMResponseError):
        summarize_commands._summarize_day_payload(
            [entry],
            DATE,
            AppConfig(),
            workspace=Path(),
        )


def test_invoke_structured_llm_uses_shared_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_builder(config: AppConfig, **_: object) -> OllamaConfig:
        captured["config"] = config.model_dump(mode="python")
        return OllamaConfig(model="builder-model")

    def fake_runner(
        config: OllamaConfig,
        prompt: str,
        *,
        system_prompt: str,
        output_type: type[DailySummary],
        prompt_path: str | None = None,
        prompt_hash: str | None = None,
        prompt_kind: str | None = None,
        prompt_set: str | None = None,
        log_label: str | None = None,
        retries: int,
    ) -> LLMResult[DailySummary]:
        assert config.model == "builder-model"
        assert "summarize" in system_prompt.lower()
        assert "entries" in prompt
        assert prompt_kind == "summarize_day"
        assert prompt_set is None
        assert retries == DEFAULT_LLM_RETRIES
        payload = output_type(
            day=DATE,
            bullets=["bullet"],
            highlights=["highlight"],
            todo_candidates=["todo"],
        )
        return LLMResult(
            model=config.model,
            prompt_path=prompt_path or "prompts/summarize_day.md",
            prompt_hash=prompt_hash,
            created_at=DATE + "T00:00:00Z",
            payload=payload,
        )

    monkeypatch.setattr(
        "aijournal.services.ollama.build_ollama_config_from_mapping",
        fake_builder,
    )
    monkeypatch.setattr("aijournal.services.ollama.run_ollama_agent", fake_runner)

    response = aijournal.services.ollama.invoke_structured_llm(
        "prompts/summarize_day.md",
        {"date": DATE, "entries_json": "[]"},
        response_model=DailySummary,
        agent_name="unit-test",
        config=AppConfig(temperature=0.3),
    )

    assert isinstance(response, DailySummary)
    assert captured["config"]["temperature"] == 0.3
