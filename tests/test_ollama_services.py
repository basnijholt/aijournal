from __future__ import annotations

from types import SimpleNamespace

import pytest

from aijournal.services import LLMResponseError, OllamaConfig, run_ollama_agent


class _FakeAgent:
    def __init__(self, output: object) -> None:
        self._output = output
        self.prompt: str | None = None

    def run_sync(self, prompt: str) -> SimpleNamespace:
        self.prompt = prompt
        return SimpleNamespace(output=self._output)


def test_run_ollama_agent_returns_payload(monkeypatch) -> None:
    agent = _FakeAgent({"ok": True})

    def fake_builder(*_: object, **__: object) -> _FakeAgent:
        return agent

    monkeypatch.setattr("aijournal.services.ollama.build_ollama_agent", fake_builder)

    result = run_ollama_agent(OllamaConfig(model="fake-model"), "prompt text")

    assert result == {"ok": True}
    assert agent.prompt == "prompt text"


def test_run_ollama_agent_raises_on_invalid_payload(monkeypatch) -> None:
    agent = _FakeAgent("not-a-dict")

    def fake_builder(*_: object, **__: object) -> _FakeAgent:
        return agent

    monkeypatch.setattr("aijournal.services.ollama.build_ollama_agent", fake_builder)

    with pytest.raises(LLMResponseError):
        run_ollama_agent(OllamaConfig(model="fake-model"), "prompt text")
