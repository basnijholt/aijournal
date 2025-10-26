"""Thin wrapper around the Ollama Python client for structured outputs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ollama import Client


class LLMResponseError(RuntimeError):
    """Raised when the Ollama response cannot be parsed as valid JSON."""


@dataclass(frozen=True)
class OllamaConfig:
    """Runtime configuration for Ollama task runners."""

    model: str
    host: str | None = None
    temperature: float | None = None
    seed: int | None = None
    max_tokens: int | None = None


class OllamaTaskRunner:
    """Convenience wrapper that requests JSON output and parses it safely."""

    def __init__(self, config: OllamaConfig) -> None:
        self._config = config
        self._client = Client(host=config.host) if config.host else Client()

    def generate_json(self, prompt: str) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if self._config.temperature is not None:
            options["temperature"] = float(self._config.temperature)
        if self._config.seed is not None:
            options["seed"] = int(self._config.seed)
        if self._config.max_tokens is not None:
            options["num_predict"] = int(self._config.max_tokens)

        response = self._client.generate(
            model=self._config.model,
            prompt=prompt,
            options=options or None,
        )
        if isinstance(response, dict):
            text = str(response.get("response") or "")
        elif hasattr(response, "model_dump"):
            data = response.model_dump()
            text = str(data.get("response") or "")
        elif hasattr(response, "response"):
            text = str(getattr(response, "response") or "")
        else:
            text = str(response)
        if not text:
            msg = "Empty response from Ollama"
            raise LLMResponseError(msg)
        return _parse_json_block(text)


JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_block(text: str) -> dict[str, Any]:
    candidate = text.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        match = JSON_BLOCK.search(candidate)
        if not match:
            msg = "Response did not contain JSON payload"
            raise LLMResponseError(msg) from None
        snippet = match.group(0)
        try:
            return json.loads(snippet)
        except json.JSONDecodeError as exc:  # pragma: no cover - unexpected format
            msg = f"Unable to parse JSON payload: {exc}"
            raise LLMResponseError(msg) from exc


__all__ = ["LLMResponseError", "OllamaConfig", "OllamaTaskRunner"]
