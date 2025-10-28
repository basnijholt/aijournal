"""Tests for `aijournal summarize` using fake Ollama outputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app
from aijournal.models import DailySummaryResponse, JournalSection, NormalizedEntry
from aijournal.services import LLMResponseError, OllamaConfig

if TYPE_CHECKING:
    from pathlib import Path

DATE = "2025-02-03"
ENTRY_ID = "2025-02-03-sync-notes"


def _has_command(name: str) -> bool:
    return any(info.name == name for info in app.registered_commands)


@pytest.fixture(autouse=True)
def skip_if_missing() -> None:
    if not _has_command("summarize"):
        pytest.skip("summarize command not available yet")


def _write_normalized(workspace: Path) -> Path:
    normalized = workspace / "data" / "normalized" / DATE / f"{ENTRY_ID}.yaml"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    normalized.write_text(
        yaml.safe_dump(
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

    data = _read_yaml(summary_path)
    assert data.get("day") == DATE
    assert isinstance(data.get("highlights"), list)
    assert isinstance(data.get("todo_candidates"), list)
    meta = data.get("meta", {})
    assert meta.get("llm_model") == "fake-ollama"
    for key in ("prompt_path", "prompt_hash", "created_at"):
        assert meta.get(key), f"Missing {key}"
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
    from aijournal import cli

    entry = NormalizedEntry(
        id="entry-1",
        created_at=f"{DATE}T09:00:00Z",
        source_path="data/journal/2025/02/03/entry-1.md",
        title="Sync Notes",
        tags=["team"],
        sections=[JournalSection(heading="Updates", level=1)],
        summary=None,
    )

    fake_response = DailySummaryResponse(
        day=DATE,
        bullets=["bullet"],
        highlights=["highlight"],
        todo_candidates=["todo"],
    )

    def fake_retry(func, *, retries: int, label: str) -> DailySummaryResponse:
        assert "summarize" in label
        return func()

    def fake_invoke(*_args, **_kwargs) -> DailySummaryResponse:
        return fake_response

    monkeypatch.setattr(cli, "_use_fake_llm", lambda: False)
    monkeypatch.setattr(cli, "_structured_call_with_retry", fake_retry)
    monkeypatch.setattr(cli, "_invoke_structured_llm", fake_invoke)

    summary = cli._summarize_day_payload([entry], DATE, {}, timeout=30.0, retries=1)

    assert summary.day == DATE
    assert summary.bullets
    assert summary.highlights
    assert summary.todo_candidates


def test_summarize_structured_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from aijournal import cli

    entry = NormalizedEntry(
        id="entry-1",
        created_at=f"{DATE}T09:00:00Z",
        source_path="data/journal/2025/02/03/entry-1.md",
        title="Sync Notes",
        tags=["team"],
        sections=[JournalSection(heading="Updates", level=1)],
        summary=None,
    )

    def fake_retry(func, *, retries: int, label: str) -> DailySummaryResponse:
        raise LLMResponseError("bad schema")

    monkeypatch.setattr(cli, "_use_fake_llm", lambda: False)
    monkeypatch.setattr(cli, "_structured_call_with_retry", fake_retry)

    with pytest.raises(LLMResponseError):
        cli._summarize_day_payload([entry], DATE, {}, timeout=30.0, retries=0)


def test_invoke_structured_llm_uses_shared_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    from aijournal import cli

    captured: dict[str, object] = {}

    def fake_builder(
        config: dict[str, object], *, timeout: float | None = None, **_: object
    ) -> OllamaConfig:
        captured["config"] = dict(config)
        captured["timeout"] = timeout
        return OllamaConfig(model="builder-model")

    def fake_runner(
        config: OllamaConfig,
        prompt: str,
        *,
        system_prompt: str,
        output_type: type[DailySummaryResponse],
    ) -> DailySummaryResponse:
        assert config.model == "builder-model"
        assert "summarize" in system_prompt.lower()
        assert "entries" in prompt
        return output_type(
            day=DATE,
            bullets=["bullet"],
            highlights=["highlight"],
            todo_candidates=["todo"],
        )

    monkeypatch.setattr(cli, "build_ollama_config_from_mapping", fake_builder)
    monkeypatch.setattr(cli, "run_ollama_agent", fake_runner)

    response = cli._invoke_structured_llm(
        "prompts/summarize_day.md",
        {"date": DATE, "entries_json": "[]"},
        response_model=DailySummaryResponse,
        agent_name="unit-test",
        config={"temperature": "0.3"},
        timeout=45.0,
    )

    assert isinstance(response, DailySummaryResponse)
    assert captured["config"] == {"temperature": "0.3"}
    assert captured["timeout"] == 45.0
