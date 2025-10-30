"""LLM helpers built on top of Pydantic AI's Ollama provider."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

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


def resolve_ollama_host(
    host: str | None = None,
    *,
    config_host: str | None = None,
) -> str:
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

    if config_host:
        return str(config_host).rstrip("/")

    return DEFAULT_OLLAMA_HOST


def resolve_ollama_base_url(
    host: str | None = None,
    *,
    config_host: str | None = None,
) -> str:
    """Return the OpenAI-compatible base URL for the Ollama provider."""
    env_base = os.getenv("OLLAMA_BASE_URL")
    if not host and env_base:
        return env_base.rstrip("/")

    base = resolve_ollama_host(host, config_host=config_host)
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
    raw_config_model = settings.get("model")
    raw_config_host = settings.get("host")

    env_model = os.getenv("AIJOURNAL_MODEL")
    resolved_model = (
        model
        or (env_model if env_model else None)
        or (str(raw_config_model) if raw_config_model else None)
        or DEFAULT_MODEL_NAME
    )

    config_host_value = str(raw_config_host) if raw_config_host else None
    resolved_host = resolve_ollama_host(host, config_host=config_host_value)
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
    retries: int | None = None,
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
    if retries is not None:
        agent_kwargs["retries"] = retries
    return Agent(build_ollama_model(config.model, config.host), **agent_kwargs)


def run_ollama_agent(
    config: OllamaConfig,
    prompt: str,
    *,
    system_prompt: str = _JSON_SYSTEM_PROMPT,
    output_type: type[Any] | None = None,
    max_attempts: int = 2,
    retry_message: str | None = None,
) -> Any:
    """Run a Pydantic AI agent and return the validated payload."""

    if max_attempts < 1:
        msg = "max_attempts must be at least 1"
        raise ValueError(msg)

    resolved_output: type[Any] = output_type or dict

    agent = build_ollama_agent(
        config,
        system_prompt=system_prompt,
        output_type=resolved_output,
        retries=max(0, max_attempts - 1),
    )

    try:
        result = agent.run_sync(prompt, output_type=resolved_output)
    except UnexpectedModelBehavior as exc:
        msg = f"Model returned invalid JSON: {exc}"
        raise LLMResponseError(msg) from exc
    except UserError as exc:
        msg = f"Ollama provider error: {exc}"
        raise LLMResponseError(msg) from exc
    except Exception as exc:  # pragma: no cover - dependent on runtime env
        msg = f"Ollama request failed: {exc}"
        raise LLMResponseError(msg) from exc

    return result.output


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
