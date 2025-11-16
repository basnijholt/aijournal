from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from aijournal.ingest_agent import (
    IngestResult,
    IngestSection,
    build_ingest_agent,
    ingest_with_agent,
)

if TYPE_CHECKING:
    from pathlib import Path


class _StubAgent:
    def __init__(self, output: object) -> None:
        self._output = output
        self.prompt: str | None = None

    def run_sync(self, prompt: str) -> SimpleNamespace:
        self.prompt = prompt
        return SimpleNamespace(output=self._output)


def test_build_ingest_agent_delegates_to_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel_config = object()
    created_agent = object()

    def fake_build_config(
        config: dict[str, object],
        *,
        model: str | None = None,
    ) -> object:
        assert config == {"temperature": 0.4}
        assert model == "custom-model"
        return sentinel_config

    def fake_build_agent(
        config: object,
        *,
        system_prompt: str,
        output_type: type[IngestResult],
        name: str,
    ) -> object:
        assert config is sentinel_config
        assert "Markdown or Hugo document" in system_prompt
        assert output_type is IngestResult
        assert name == "aijournal-ingest"
        return created_agent

    monkeypatch.setattr(
        "aijournal.ingest_agent.build_ollama_config_from_mapping",
        fake_build_config,
    )
    monkeypatch.setattr("aijournal.ingest_agent.build_ollama_agent", fake_build_agent)

    agent = build_ingest_agent({"temperature": 0.4}, model="custom-model")

    assert agent is created_agent


def test_ingest_with_agent_returns_structured_payload(tmp_path: Path) -> None:
    expected = IngestResult(
        entry_id="2024-10-01-morning-notes",
        created_at="2024-10-01T09:00:00Z",
        title="Morning notes",
        tags=["focus"],
        sections=[IngestSection(heading="Start of day", level=2, summary="Planned focus blocks")],
        summary="Outlined the day.",
    )
    agent = _StubAgent(expected)
    source = tmp_path / "entry.md"
    prompt_result = ingest_with_agent(agent, source_path=source, markdown="## Start of day")

    assert prompt_result == expected
    assert agent.prompt is not None
    assert str(source) in agent.prompt
    assert agent.prompt.count("---BEGIN DOCUMENT---") == 1
    assert agent.prompt.endswith("---END DOCUMENT---")


def test_ingest_with_agent_rejects_unexpected_payload(tmp_path: Path) -> None:
    agent = _StubAgent({"entry_id": "example"})

    with pytest.raises(ValueError, match="expected structured payload"):
        ingest_with_agent(agent, source_path=tmp_path / "entry.md", markdown="content")
