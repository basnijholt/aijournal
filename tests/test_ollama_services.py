from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from pydantic_ai import ModelSettings, UnexpectedModelBehavior
from pydantic_ai.exceptions import UserError

from aijournal.services import (
    LLMResponseError,
    OllamaConfig,
    build_ollama_agent,
    build_ollama_config_from_mapping,
    resolve_ollama_base_url,
    resolve_ollama_host,
    run_ollama_agent,
)


class _FakeAgent:
    def __init__(self, texts: list[str], raise_error: Exception | None = None) -> None:
        self._texts = texts
        self._raise_error = raise_error
        self.prompt: str | None = None
        self.calls = 0

    def run_sync(self, prompt: str, output_type: object | None = None) -> SimpleNamespace:
        self.prompt = prompt
        if self._raise_error is not None:
            raise self._raise_error

        text = self._texts[min(self.calls, len(self._texts) - 1)]
        self.calls += 1

        try:
            if isinstance(output_type, type) and issubclass(output_type, BaseModel):
                payload = json.loads(text)
                return SimpleNamespace(output=output_type.model_validate(payload))
            if output_type is dict:
                payload = json.loads(text)
                if not isinstance(payload, dict):
                    raise ValueError("expected dict payload")
                return SimpleNamespace(output=payload)
        except Exception as exc:  # pragma: no cover - test helper only
            raise UnexpectedModelBehavior(str(exc)) from exc

        return SimpleNamespace(output=text)


def test_run_ollama_agent_returns_payload(monkeypatch) -> None:
    agent = _FakeAgent(['{"ok": true}'])

    def fake_builder(*_: object, **__: object) -> _FakeAgent:
        return agent

    monkeypatch.setattr("aijournal.services.ollama.build_ollama_agent", fake_builder)

    result = run_ollama_agent(OllamaConfig(model="fake-model"), "prompt text")

    assert result == {"ok": True}
    assert agent.prompt == "prompt text"


def test_run_ollama_agent_raises_on_invalid_payload(monkeypatch) -> None:
    agent = _FakeAgent(['["unexpected"]'])

    def fake_builder(*_: object, **__: object) -> _FakeAgent:
        return agent

    monkeypatch.setattr("aijournal.services.ollama.build_ollama_agent", fake_builder)

    with pytest.raises(LLMResponseError):
        run_ollama_agent(OllamaConfig(model="fake-model"), "prompt text")


def test_run_ollama_agent_strips_markdown_fences(monkeypatch) -> None:
    agent = _FakeAgent(['```json\n{\n  "ok": true\n}\n``` extra text'])

    def fake_builder(*_: object, **__: object) -> _FakeAgent:
        return agent

    monkeypatch.setattr("aijournal.services.ollama.build_ollama_agent", fake_builder)

    with pytest.raises(LLMResponseError, match="Model returned invalid JSON"):
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


def test_resolve_ollama_host_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_OLLAMA_HOST", "http://env-host/")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://base-host/v1")

    assert resolve_ollama_host(None) == "http://env-host"
    assert resolve_ollama_host("http://override/") == "http://override"


def test_resolve_ollama_host_from_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIJOURNAL_OLLAMA_HOST", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://base-host/v1")

    assert resolve_ollama_host(None) == "http://base-host"


def test_resolve_ollama_base_url_appends_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    assert resolve_ollama_base_url("http://host") == "http://host/v1"
    assert resolve_ollama_base_url("http://host/v1") == "http://host/v1"


def test_run_ollama_agent_translates_user_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingAgent:
        def run_sync(self, prompt: str, **_: object) -> SimpleNamespace:
            raise UserError("bad request")

    monkeypatch.setattr(
        "aijournal.services.ollama.build_ollama_agent", lambda *_, **__: FailingAgent()
    )

    with pytest.raises(LLMResponseError, match="Ollama provider error: bad request"):
        run_ollama_agent(OllamaConfig(model="fake"), "prompt")


def test_run_ollama_agent_translates_unexpected_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingAgent:
        def run_sync(self, prompt: str, **_: object) -> SimpleNamespace:
            raise UnexpectedModelBehavior("bad")

    monkeypatch.setattr(
        "aijournal.services.ollama.build_ollama_agent", lambda *_, **__: FailingAgent()
    )

    with pytest.raises(LLMResponseError, match="Model returned invalid JSON: bad"):
        run_ollama_agent(OllamaConfig(model="fake"), "prompt")


def test_run_ollama_agent_rejects_empty_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _FakeAgent(["   "])

    def fake_builder(*_: object, **__: object) -> _FakeAgent:
        return agent

    monkeypatch.setattr("aijournal.services.ollama.build_ollama_agent", fake_builder)

    with pytest.raises(LLMResponseError, match="Model returned invalid JSON"):
        run_ollama_agent(OllamaConfig(model="fake"), "prompt")


def test_run_ollama_agent_handles_extra_commentary(monkeypatch: pytest.MonkeyPatch) -> None:
    text = '{"ok": true} trailing commentary that should be trimmed'
    agent = _FakeAgent([text])

    def fake_builder(*_: object, **__: object) -> _FakeAgent:
        return agent

    monkeypatch.setattr("aijournal.services.ollama.build_ollama_agent", fake_builder)

    with pytest.raises(LLMResponseError, match="Model returned invalid JSON"):
        run_ollama_agent(OllamaConfig(model="fake-model"), "prompt")


def test_build_ollama_agent_injects_model_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class DummyAgent:
        def __init__(self, model: object, **kwargs: object) -> None:
            captured["model"] = model
            captured["kwargs"] = kwargs

    monkeypatch.setattr("aijournal.services.ollama.Agent", DummyAgent)
    monkeypatch.setattr(
        "aijournal.services.ollama.build_ollama_model", lambda name, host: (name, host)
    )

    config = OllamaConfig(
        model="model-name",
        host="http://host",
        temperature=0.2,
        seed=42,
        max_tokens=512,
        timeout=30.0,
    )

    agent = build_ollama_agent(config, system_prompt="prompt")

    assert isinstance(agent, DummyAgent)
    assert captured["model"] == ("model-name", "http://host")
    kwargs = captured["kwargs"]
    assert kwargs["system_prompt"] == "prompt"
    assert kwargs["name"] == "aijournal-json-runner"
    assert "output_type" not in kwargs
    model_settings = kwargs["model_settings"]
    expected_settings = ModelSettings(
        temperature=0.2,
        seed=42,
        max_tokens=512,
        timeout=30.0,
    )
    assert model_settings == expected_settings
