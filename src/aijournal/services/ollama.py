"""LLM helpers built on top of Pydantic AI's Ollama provider."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic_ai import Agent, ModelSettings, UnexpectedModelBehavior
from pydantic_ai.exceptions import UserError
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider

from aijournal.utils.coercion import coerce_float, coerce_int

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL_NAME = "gpt-oss:20b"
_JSON_SYSTEM_PROMPT = (
    "You are part of the aijournal CLI. "
    "Respond with valid JSON only—no markdown fences, explanations, or trailing text."
)

_logger = logging.getLogger(__name__)

_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_JSON_START_PATTERN = re.compile(r"[{\[]")
_MAX_LOG_PAYLOAD_CHARS = 2000


class LLMResponseError(RuntimeError):
    """Raised when the LLM response cannot be parsed as valid JSON."""


@dataclass(frozen=True)
class OllamaConfig:
    """Runtime configuration for Ollama task runners."""

    model: str
    host: str | None = None
    temperature: float | None = None
    seed: int | None = None
    max_tokens: int | None = None
    timeout: float | None = None


def resolve_ollama_host(host: str | None = None) -> str:
    """Return the base Ollama host (without `/v1`) to contact."""
    if host:
        return host.rstrip("/")
    env_host = os.getenv("AIJOURNAL_OLLAMA_HOST")
    if env_host:
        return env_host.rstrip("/")
    env_base = os.getenv("OLLAMA_BASE_URL")
    if env_base:
        candidate = env_base.rstrip("/")
        if candidate.endswith("/v1"):
            candidate = candidate.removesuffix("/v1")
        return candidate
    return DEFAULT_OLLAMA_HOST


def resolve_ollama_base_url(host: str | None = None) -> str:
    """Return the OpenAI-compatible base URL for the Ollama provider."""
    env_base = os.getenv("OLLAMA_BASE_URL")
    if not host and env_base:
        return env_base.rstrip("/")

    base = resolve_ollama_host(host)
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def build_ollama_model(model_name: str, host: str | None = None) -> OpenAIChatModel:
    """Create an OpenAIChatModel configured for the target Ollama endpoint."""
    provider = OllamaProvider(base_url=resolve_ollama_base_url(host))
    return OpenAIChatModel(model_name=model_name, provider=provider)


def build_ollama_config_from_mapping(
    config: Mapping[str, Any] | None = None,
    *,
    model: str | None = None,
    host: str | None = None,
    timeout: float | None = None,
) -> OllamaConfig:
    """Construct an OllamaConfig from a loose mapping of settings."""

    settings = config or {}
    resolved_model = model or str(
        settings.get("model") or os.getenv("AIJOURNAL_MODEL") or DEFAULT_MODEL_NAME
    )
    resolved_host = host or os.getenv("AIJOURNAL_OLLAMA_HOST")
    temperature = coerce_float(settings.get("temperature"))
    seed = coerce_int(settings.get("seed"))
    max_tokens = coerce_int(settings.get("max_tokens"))
    effective_timeout = timeout if timeout is not None else coerce_float(settings.get("timeout"))
    return OllamaConfig(
        model=resolved_model,
        host=resolved_host,
        temperature=temperature,
        seed=seed,
        max_tokens=max_tokens,
        timeout=effective_timeout,
    )


def _model_settings_from_config(config: OllamaConfig) -> ModelSettings | None:
    kwargs: dict[str, Any] = {}
    if config.temperature is not None:
        kwargs["temperature"] = float(config.temperature)
    if config.seed is not None:
        kwargs["seed"] = int(config.seed)
    if config.max_tokens is not None:
        kwargs["max_tokens"] = int(config.max_tokens)
    if config.timeout is not None:
        kwargs["timeout"] = float(config.timeout)
    return ModelSettings(**cast(Any, kwargs)) if kwargs else None


def build_ollama_agent(
    config: OllamaConfig,
    *,
    system_prompt: str = _JSON_SYSTEM_PROMPT,
    output_type: type[Any] | None = None,
    name: str = "aijournal-json-runner",
) -> Agent:
    """Create a Pydantic AI agent for the given configuration."""
    model_settings = _model_settings_from_config(config)
    agent_kwargs: dict[str, Any] = {
        "name": name,
        "system_prompt": system_prompt,
        "model_settings": model_settings,
    }
    if output_type is not None:
        agent_kwargs["output_type"] = output_type
    return Agent(
        build_ollama_model(config.model, config.host),
        **agent_kwargs,
    )


def _trim_json_suffix(payload: str) -> str:
    """Trim trailing commentary after the first balanced JSON object/array."""
    stack: list[str] = []
    in_string = False
    escape = False
    for idx, ch in enumerate(payload):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch in "{[":
            stack.append(ch)
            continue

        if ch in "]}":
            if not stack:
                break
            opener = stack.pop()
            if (opener == "{" and ch != "}") or (opener == "[" and ch != "]"):
                break
            if not stack:
                return payload[: idx + 1].strip()

    return payload.strip()


def _sanitize_json_payload(raw_text: str) -> str:
    """Remove markdown fences and stray commentary to isolate JSON."""
    text = raw_text.strip()
    if not text:
        return ""

    fence_match = _FENCE_PATTERN.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    start_match = _JSON_START_PATTERN.search(text)
    if start_match:
        text = text[start_match.start() :].strip()

    return _trim_json_suffix(text)


def _log_payload_failure(cleaned: str) -> None:
    snippet = cleaned
    if len(snippet) > _MAX_LOG_PAYLOAD_CHARS:
        snippet = f"{snippet[:_MAX_LOG_PAYLOAD_CHARS]}… [truncated]"
    _logger.error("LLM JSON payload failed validation:\n%s", snippet)


def _validate_json_payload(cleaned: str, output_type: type[Any]) -> Any:
    """Convert sanitized JSON text into the requested Python payload."""
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        _log_payload_failure(cleaned)
        msg = f"Model returned invalid JSON: {exc}"
        raise LLMResponseError(msg) from exc

    if isinstance(output_type, type) and issubclass(output_type, BaseModel):
        try:
            return output_type.model_validate(data)
        except ValidationError as exc:
            _log_payload_failure(cleaned)
            msg = f"Model response did not match schema {output_type.__name__}: {exc}"
            raise LLMResponseError(msg) from exc

    if output_type is dict:
        if isinstance(data, dict):
            return data
        _log_payload_failure(cleaned)
        msg = f"Expected dict payload but received {type(data).__name__}"
        raise LLMResponseError(msg)

    if output_type is Any:
        return data

    adapter = TypeAdapter(output_type)
    try:
        return adapter.validate_python(data)
    except ValidationError as exc:
        _log_payload_failure(cleaned)
        msg = f"Model response did not match expected type {output_type}: {exc}"
        raise LLMResponseError(msg) from exc


def run_ollama_agent(
    config: OllamaConfig,
    prompt: str,
    *,
    system_prompt: str = _JSON_SYSTEM_PROMPT,
    output_type: type[Any] | None = None,
) -> Any:
    """Run a short-lived agent and return the structured payload."""
    resolved_output: type[Any] = output_type or dict
    agent = build_ollama_agent(
        config,
        system_prompt=system_prompt,
        output_type=None,
    )
    try:
        result = agent.run_sync(prompt, output_type=None)
    except UnexpectedModelBehavior as exc:
        msg = f"Model returned invalid JSON: {exc}"
        raise LLMResponseError(msg) from exc
    except UserError as exc:
        msg = f"Ollama provider error: {exc}"
        raise LLMResponseError(msg) from exc
    except Exception as exc:  # pragma: no cover - dependent on runtime env
        msg = f"Ollama request failed: {exc}"
        raise LLMResponseError(msg) from exc

    response = result.response
    raw_text = (response.text or "").strip() if response else ""
    cleaned = _sanitize_json_payload(raw_text)
    if not cleaned:
        _log_payload_failure(raw_text)
        msg = "Model returned an empty response when JSON was required"
        raise LLMResponseError(msg)

    return _validate_json_payload(cleaned, resolved_output)


__all__ = [
    "LLMResponseError",
    "OllamaConfig",
    "build_ollama_model",
    "build_ollama_agent",
    "build_ollama_config_from_mapping",
    "run_ollama_agent",
    "resolve_ollama_base_url",
    "resolve_ollama_host",
]
