from __future__ import annotations

from types import SimpleNamespace

import pytest

from aijournal.services import (
    LLMResponseError,
    OllamaConfig,
    build_ollama_config_from_mapping,
    run_ollama_agent,
)


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


def test_build_config_coerces_numeric_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIJOURNAL_MODEL", raising=False)
    monkeypatch.delenv("AIJOURNAL_OLLAMA_HOST", raising=False)
    config = {
        "model": "llama3.1:8b-instruct",
        "temperature": "0.45",
        "seed": "99",
        "max_tokens": "2048",
    }

    result = build_ollama_config_from_mapping(config)

    assert result.model == "llama3.1:8b-instruct"
    assert result.temperature == pytest.approx(0.45)
    assert result.seed == 99
    assert result.max_tokens == 2048
    assert result.timeout is None


def test_build_config_prefers_explicit_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIJOURNAL_MODEL", raising=False)
    monkeypatch.delenv("AIJOURNAL_OLLAMA_HOST", raising=False)
    config = {
        "model": "config-model",
        "temperature": 0.2,
        "seed": 7,
    }

    result = build_ollama_config_from_mapping(
        config,
        model="override-model",
        host="http://override-host:11434",
        timeout=30.0,
    )

    assert result.model == "override-model"
    assert result.host == "http://override-host:11434"
    assert result.temperature == pytest.approx(0.2)
    assert result.seed == 7
    assert result.timeout == pytest.approx(30.0)


def test_build_config_respects_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_MODEL", "env-model")
    monkeypatch.setenv("AIJOURNAL_OLLAMA_HOST", "http://env-host")
    config: dict[str, object] = {}

    result = build_ollama_config_from_mapping(config)

    assert result.model == "env-model"
    assert result.host == "http://env-host"
