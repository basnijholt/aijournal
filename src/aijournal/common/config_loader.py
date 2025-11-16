"""Configuration loading utilities."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import yaml

from aijournal.common.app_config import AppConfig, LLMConfig

if TYPE_CHECKING:
    from pathlib import Path


def load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML file and return as dictionary.

    Args:
        path: Path to YAML file

    Returns:
        Parsed YAML dictionary, or empty dict if file is empty

    """
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_config(workspace: Path) -> AppConfig:
    """Load configuration from workspace/config.yaml.

    Args:
        workspace: The workspace directory containing config.yaml

    Returns:
        Parsed AppConfig or defaults if config.yaml doesn't exist

    """
    config_path = workspace / "config.yaml"
    if not config_path.exists():
        return AppConfig()

    data = load_yaml(config_path)
    return AppConfig.model_validate(data)


def load_config_with_overrides(
    workspace: Path,
    *,
    llm_retries: int | None = None,
    llm_timeout: float | None = None,
) -> AppConfig:
    """Load workspace config and apply runtime LLM overrides."""
    config = load_config(workspace)
    return apply_llm_overrides(config, retries=llm_retries, timeout=llm_timeout)


def apply_llm_overrides(
    config: AppConfig,
    *,
    retries: int | None = None,
    timeout: float | None = None,
) -> AppConfig:
    """Return a copy of the config with CLI LLM overrides applied."""
    updates: dict[str, Any] = {}
    llm_updates: dict[str, Any] = {}
    if retries is not None:
        llm_updates["retries"] = retries
    if timeout is not None:
        llm_updates["timeout"] = timeout
    if llm_updates:
        llm_data = config.llm.model_dump(mode="python")
        llm_data.update(llm_updates)
        validated_llm = LLMConfig.model_validate(llm_data)
        updates["llm"] = validated_llm
    if updates:
        return config.model_copy(update=updates)
    return config


def use_fake_llm() -> bool:
    """Check if fake LLM mode is enabled via environment variable.

    Returns:
        True if AIJOURNAL_FAKE_OLLAMA=1, False otherwise

    """
    return os.getenv("AIJOURNAL_FAKE_OLLAMA") == "1"


def resolve_prompt_set(
    *,
    cli_override: str | None = None,
    config: AppConfig | None = None,
) -> str | None:
    """Resolve the active prompt set for A/B/N testing.

    Precedence (highest to lowest):
    1. CLI flag (--prompt-set)
    2. Environment variable (AIJOURNAL_PROMPT_SET)
    3. Config file (prompts.active_set)

    Args:
        cli_override: Optional CLI flag value
        config: App configuration

    Returns:
        Active prompt set name, or None for default prompts

    """
    if cli_override:
        return cli_override

    env_set = os.getenv("AIJOURNAL_PROMPT_SET")
    if env_set:
        return env_set

    if config and config.prompts.active_set:
        return config.prompts.active_set

    return None
