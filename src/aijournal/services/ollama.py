"""Thin wrapper around the Ollama Python client for structured outputs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ollama import Client


class LLMResponseError(RuntimeError):
    """Raised when the Ollama response cannot be parsed as valid JSON."""


@dataclass(frozen=True)
class OllamaConfig:
    """Runtime configuration for Ollama task runners."""

    model: str
    host: Optional[str] = None
    temperature: Optional[float] = None
    seed: Optional[int] = None
    max_tokens: Optional[int] = None


class OllamaTaskRunner:
    """Convenience wrapper that requests JSON output and parses it safely."""

    def __init__(self, config: OllamaConfig) -> None:
        self._config = config
        self._client = Client(host=config.host) if config.host else Client()

    def generate_json(self, prompt: str) -> Dict[str, Any]:
        options: Dict[str, Any] = {}
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
        text = response.get("response") if isinstance(response, dict) else str(response)
        if not text:
            raise LLMResponseError("Empty response from Ollama")
        return _parse_json_block(text)


JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_block(text: str) -> Dict[str, Any]:
    candidate = text.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        match = JSON_BLOCK.search(candidate)
        if not match:
            raise LLMResponseError("Response did not contain JSON payload") from None
        snippet = match.group(0)
        try:
            return json.loads(snippet)
        except json.JSONDecodeError as exc:  # pragma: no cover - unexpected format
            raise LLMResponseError(f"Unable to parse JSON payload: {exc}") from exc


__all__ = ["LLMResponseError", "OllamaConfig", "OllamaTaskRunner"]
