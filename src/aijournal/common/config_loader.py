"""Configuration loading utilities."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from aijournal.common.app_config import AppConfig


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


def use_fake_llm() -> bool:
    """Check if fake LLM mode is enabled via environment variable.

    Returns:
        True if AIJOURNAL_FAKE_OLLAMA=1, False otherwise
    """
    return os.getenv("AIJOURNAL_FAKE_OLLAMA") == "1"
