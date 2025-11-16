from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel
from pydantic_ai import ModelSettings, UnexpectedModelBehavior
from pydantic_ai.exceptions import UserError

from aijournal.common.app_config import AppConfig
from aijournal.services.ollama import (
    LLMResponseError,
    OllamaConfig,
    build_ollama_agent,
    build_ollama_config_from_mapping,
    resolve_ollama_base_url,
    resolve_ollama_host,
    run_ollama_agent,
)

if TYPE_CHECKING:
    from pathlib import Path

    from aijournal.common.meta import LLMResult


class _FakeResult(SimpleNamespace):
    def __init__(self, output: object, requests: int) -> None:
        super().__init__(output=output)
        self._requests = requests

    def usage(self) -> SimpleNamespace:  # pragma: no cover - tiny helper
        return SimpleNamespace(requests=self._requests)


class _FakeAgent:
    def __init__(self, texts: list[str], raise_error: Exception | None = None) -> None:
        self._texts = texts
        self._raise_error = raise_error
        self.prompt: str | None = None
        self.calls = 0
        self.name = "fake-agent"

    def run_sync(self, prompt: str, output_type: object | None = None) -> SimpleNamespace:
        self.prompt = prompt
        if self._raise_error is not None:
            raise self._raise_error

        text = self._texts[min(self.calls, len(self._texts) - 1)]
        self.calls += 1

        try:
            if isinstance(output_type, type) and issubclass(output_type, BaseModel):
                payload = json.loads(text)
                validated = output_type.model_validate(payload)
                return _FakeResult(validated, self.calls)
            if output_type is dict:
                payload = json.loads(text)
                if not isinstance(payload, dict):
                    msg = "expected dict payload"
                    raise ValueError(msg)
                return _FakeResult(payload, self.calls)
        except Exception as exc:  # pragma: no cover - helper safety
            raise UnexpectedModelBehavior(str(exc)) from exc

        return _FakeResult(text, self.calls)


class _ListModel(BaseModel):
    names: list[str]


def test_run_ollama_agent_returns_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    agent = _FakeAgent(['{"ok": true}'])

    monkeypatch.setattr("aijournal.services.ollama.build_ollama_agent", lambda *_, **__: agent)

    result: LLMResult[dict[str, object]] = run_ollama_agent(
        OllamaConfig(model="fake-model"),
        "prompt text",
        prompt_path="prompts/example.md",
        prompt_hash="hash",
    )

    assert result.payload == {"ok": True}
    assert result.prompt_path == "prompts/example.md"
    assert result.prompt_hash == "hash"
    assert result.attempts == 1
    assert result.coercions_applied == []
    assert agent.prompt == "prompt text"

    metrics_path = tmp_path / "derived" / "logs" / "structured_metrics.jsonl"
    metrics = [
        json.loads(line)
        for line in metrics_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert metrics[-1]["attempts"] == 1
    assert metrics[-1]["coercion_count"] == 0


def test_run_ollama_agent_logs_on_invalid_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    agent = _FakeAgent(['["unexpected"]'])

    monkeypatch.setattr("aijournal.services.ollama.build_ollama_agent", lambda *_, **__: agent)

    with pytest.raises(LLMResponseError):
        run_ollama_agent(
            OllamaConfig(model="fake-model"),
            "prompt text",
            prompt_path="prompts/test.md",
            prompt_hash="hash",
        )

    failure_dir = tmp_path / "derived" / "logs" / "structured_failures" / "fake-agent"
    logs = list(failure_dir.glob("*.json"))
    assert logs, "structured failure should have been logged"


def test_run_ollama_agent_translates_user_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    class FailingAgent:
        name = "failing"

        def run_sync(self, prompt: str, **_: object) -> SimpleNamespace:
            msg = "bad request"
            raise UserError(msg)

    monkeypatch.setattr(
        "aijournal.services.ollama.build_ollama_agent",
        lambda *_, **__: FailingAgent(),
    )

    with pytest.raises(LLMResponseError, match="Ollama provider error: bad request"):
        run_ollama_agent(
            OllamaConfig(model="fake"),
            "prompt",
            prompt_path="prompts/test.md",
        )


def test_run_ollama_agent_translates_unexpected_behavior(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    class FailingAgent:
        name = "failing"

        def run_sync(self, prompt: str, **_: object) -> SimpleNamespace:
            msg = "bad"
            raise UnexpectedModelBehavior(msg)

    monkeypatch.setattr(
        "aijournal.services.ollama.build_ollama_agent",
        lambda *_, **__: FailingAgent(),
    )

    with pytest.raises(LLMResponseError, match="Model returned invalid JSON: bad"):
        run_ollama_agent(
            OllamaConfig(model="fake"),
            "prompt",
            prompt_path="prompts/test.md",
        )


def test_run_ollama_agent_rejects_empty_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    agent = _FakeAgent(["   "])

    monkeypatch.setattr("aijournal.services.ollama.build_ollama_agent", lambda *_, **__: agent)

    with pytest.raises(LLMResponseError, match="Model returned invalid JSON"):
        run_ollama_agent(
            OllamaConfig(model="fake"),
            "prompt",
            prompt_path="prompts/test.md",
        )


def test_run_ollama_agent_handles_extra_commentary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    text = '{"ok": true} trailing commentary that should be trimmed'
    agent = _FakeAgent([text])

    monkeypatch.setattr("aijournal.services.ollama.build_ollama_agent", lambda *_, **__: agent)

    with pytest.raises(LLMResponseError, match="Model returned invalid JSON"):
        run_ollama_agent(
            OllamaConfig(model="fake-model"),
            "prompt",
            prompt_path="prompts/test.md",
        )


def test_run_ollama_agent_strips_markdown_fences(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    agent = _FakeAgent(['```json\n{\n  "ok": true\n}\n``` extra text'])

    monkeypatch.setattr("aijournal.services.ollama.build_ollama_agent", lambda *_, **__: agent)

    with pytest.raises(LLMResponseError, match="Model returned invalid JSON"):
        run_ollama_agent(
            OllamaConfig(model="fake-model"),
            "prompt text",
            prompt_path="prompts/test.md",
        )


def test_run_ollama_agent_coerces_scalar_lists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    agent = _FakeAgent(['{"names": "solo"}'])

    monkeypatch.setattr("aijournal.services.ollama.build_ollama_agent", lambda *_, **__: agent)

    result: LLMResult[_ListModel] = run_ollama_agent(
        OllamaConfig(model="fake-model"),
        "prompt",
        output_type=_ListModel,
        prompt_path="prompts/test.md",
    )

    assert result.payload.names == ["solo"]
    assert result.coercions_applied
    assert result.coercions_applied[0]["rule"] == "wrap_scalar_in_list"
    assert "JSON_SKELETON" in (agent.prompt or "")

    metrics_path = tmp_path / "derived" / "logs" / "structured_metrics.jsonl"
    metrics = [
        json.loads(line)
        for line in metrics_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert metrics[-1]["coercion_count"] == 1


def test_build_config_coerces_numeric_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIJOURNAL_MODEL", raising=False)
    monkeypatch.delenv("AIJOURNAL_OLLAMA_HOST", raising=False)
    config_dict = {
        "model": "llama3.1:8b-instruct",
        "temperature": "0.45",
        "seed": "99",
        "max_tokens": "2048",
    }
    config = AppConfig.model_validate(config_dict)

    result = build_ollama_config_from_mapping(config)

    assert result.model == "llama3.1:8b-instruct"
    assert result.temperature == pytest.approx(0.45)
    assert result.seed == 99
    assert result.max_tokens == 2048
    assert result.timeout is None
    assert result.host == "http://127.0.0.1:11434"


def test_build_config_prefers_explicit_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIJOURNAL_MODEL", raising=False)
    monkeypatch.delenv("AIJOURNAL_OLLAMA_HOST", raising=False)
    config_dict = {
        "model": "config-model",
        "temperature": 0.2,
        "seed": 7,
    }
    config = AppConfig.model_validate(config_dict)

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
    config_dict: dict[str, object] = {"model": "config-model", "host": "http://config-host"}
    config = AppConfig.model_validate(config_dict)

    result = build_ollama_config_from_mapping(config)

    assert result.model == "env-model"
    assert result.host == "http://env-host"


def test_build_config_uses_config_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIJOURNAL_MODEL", raising=False)
    monkeypatch.delenv("AIJOURNAL_OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    config_dict = {"host": "http://config-host:11434", "model": "config-model"}
    config = AppConfig.model_validate(config_dict)

    result = build_ollama_config_from_mapping(config)

    assert result.host == "http://config-host:11434"
    assert result.model == "config-model"


def test_environment_model_overrides_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_MODEL", "env-model")
    monkeypatch.delenv("AIJOURNAL_OLLAMA_HOST", raising=False)
    config_dict = {"model": "config-model"}
    config = AppConfig.model_validate(config_dict)

    result = build_ollama_config_from_mapping(config)

    assert result.model == "env-model"


def test_resolve_ollama_host_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_OLLAMA_HOST", "http://env-host/")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://base-host/v1")

    assert resolve_ollama_host(None) == "http://env-host"
    assert resolve_ollama_host("http://override/") == "http://override"


def test_resolve_ollama_host_from_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIJOURNAL_OLLAMA_HOST", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://base-host/v1")

    assert resolve_ollama_host(None) == "http://base-host"


def test_resolve_ollama_host_uses_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIJOURNAL_OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    assert (
        resolve_ollama_host(None, config_host="http://config-host:11434")
        == "http://config-host:11434"
    )


def test_resolve_ollama_base_url_appends_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    assert resolve_ollama_base_url("http://host") == "http://host/v1"
    assert resolve_ollama_base_url("http://host/v1") == "http://host/v1"


def test_build_ollama_agent_injects_model_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class DummyAgent:
        def __init__(self, model: object, **kwargs: object) -> None:
            captured["model"] = model
            captured["kwargs"] = kwargs

    monkeypatch.setattr("aijournal.services.ollama.Agent", DummyAgent)
    monkeypatch.setattr(
        "aijournal.services.ollama.build_ollama_model",
        lambda name, host: (name, host),
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
