"""Tests for prompt-set A/B/N testing mechanism."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest import mock

from aijournal.common.app_config import AppConfig, PromptsConfig
from aijournal.common.config_loader import resolve_prompt_set
from aijournal.utils.paths import resolve_prompt_path

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class TestResolvePromptSet:
    """Tests for resolve_prompt_set precedence logic."""

    def test_cli_override_takes_precedence(self) -> None:
        """CLI flag should override env and config."""
        config = AppConfig(prompts=PromptsConfig(active_set="config-set"))
        with mock.patch.dict(os.environ, {"AIJOURNAL_PROMPT_SET": "env-set"}):
            result = resolve_prompt_set(cli_override="cli-set", config=config)
            assert result == "cli-set"

    def test_env_overrides_config(self) -> None:
        """Environment variable should override config."""
        config = AppConfig(prompts=PromptsConfig(active_set="config-set"))
        with mock.patch.dict(os.environ, {"AIJOURNAL_PROMPT_SET": "env-set"}):
            result = resolve_prompt_set(config=config)
            assert result == "env-set"

    def test_config_fallback(self) -> None:
        """Config value should be used when no CLI or env override."""
        config = AppConfig(prompts=PromptsConfig(active_set="config-set"))
        with mock.patch.dict(os.environ, {}, clear=True):
            result = resolve_prompt_set(config=config)
            assert result == "config-set"

    def test_returns_none_when_no_sources(self) -> None:
        """Should return None when no prompt set is specified anywhere."""
        config = AppConfig(prompts=PromptsConfig(active_set=None))
        with mock.patch.dict(os.environ, {}, clear=True):
            result = resolve_prompt_set(config=config)
            assert result is None

    def test_no_config_provided(self) -> None:
        """Should handle None config gracefully."""
        with mock.patch.dict(os.environ, {}, clear=True):
            result = resolve_prompt_set(config=None)
            assert result is None

    def test_cli_override_alone(self) -> None:
        """CLI override should work without config."""
        result = resolve_prompt_set(cli_override="cli-set", config=None)
        assert result == "cli-set"


class TestResolvePromptPath:
    """Tests for resolve_prompt_path with experiment overrides."""

    def test_absolute_path_returns_as_is(self, tmp_path: Path) -> None:
        """Absolute paths should be returned unchanged."""
        absolute = tmp_path / "custom" / "prompt.md"
        result = resolve_prompt_path(str(absolute))
        assert result == absolute

    def test_experiment_override_in_cwd(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should find experiment override in cwd when it exists."""
        monkeypatch.chdir(tmp_path)

        # Create experiment override
        experiment_dir = tmp_path / "prompts" / "experiments" / "variant-a"
        experiment_dir.mkdir(parents=True)
        override_file = experiment_dir / "summarize_day.md"
        override_file.write_text("Experiment variant A")

        # Create default prompt
        default_dir = tmp_path / "prompts"
        default_dir.mkdir(exist_ok=True)
        default_file = default_dir / "summarize_day.md"
        default_file.write_text("Default prompt")

        result = resolve_prompt_path("prompts/summarize_day.md", prompt_set="variant-a")
        assert result == override_file
        assert result.read_text() == "Experiment variant A"

    def test_falls_back_to_default_when_no_experiment(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should fall back to default prompt when experiment override doesn't exist."""
        monkeypatch.chdir(tmp_path)

        # Create only default prompt
        default_dir = tmp_path / "prompts"
        default_dir.mkdir(parents=True)
        default_file = default_dir / "summarize_day.md"
        default_file.write_text("Default prompt")

        result = resolve_prompt_path("prompts/summarize_day.md", prompt_set="nonexistent")
        assert result == default_file
        assert result.read_text() == "Default prompt"

    def test_no_prompt_set_uses_default(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should use default prompt when prompt_set is None."""
        monkeypatch.chdir(tmp_path)

        default_dir = tmp_path / "prompts"
        default_dir.mkdir(parents=True)
        default_file = default_dir / "summarize_day.md"
        default_file.write_text("Default prompt")

        result = resolve_prompt_path("prompts/summarize_day.md", prompt_set=None)
        assert result == default_file

    def test_extracts_filename_correctly(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Should extract prompt kind correctly from path."""
        monkeypatch.chdir(tmp_path)

        # Create experiment with nested path input
        experiment_dir = tmp_path / "prompts" / "experiments" / "test-set"
        experiment_dir.mkdir(parents=True)
        override_file = experiment_dir / "extract_facts.md"
        override_file.write_text("Experiment override")

        result = resolve_prompt_path("prompts/extract_facts.md", prompt_set="test-set")
        assert result == override_file


class TestPromptsConfig:
    """Tests for PromptsConfig model."""

    def test_default_active_set_is_none(self) -> None:
        """Default active_set should be None."""
        config = PromptsConfig()
        assert config.active_set is None

    def test_can_set_active_set(self) -> None:
        """Should allow setting active_set."""
        config = PromptsConfig(active_set="experiment-1")
        assert config.active_set == "experiment-1"

    def test_ignore_extra_fields(self) -> None:
        """Should ignore extra fields."""
        config = PromptsConfig(active_set="test", unknown_field="value")  # type: ignore[call-arg]
        assert config.model_dump() == {"active_set": "test"}


class TestAppConfigIntegration:
    """Tests for AppConfig with prompts section."""

    def test_app_config_includes_prompts(self) -> None:
        """AppConfig should include prompts configuration."""
        config = AppConfig()
        assert hasattr(config, "prompts")
        assert isinstance(config.prompts, PromptsConfig)

    def test_can_set_prompts_active_set_via_dict(self) -> None:
        """Should parse prompts.active_set from dict."""
        data = {"prompts": {"active_set": "variant-b"}}
        config = AppConfig.model_validate(data)
        assert config.prompts.active_set == "variant-b"

    def test_prompts_defaults_when_not_specified(self) -> None:
        """Prompts config should use defaults when not in yaml."""
        config = AppConfig.model_validate({})
        assert config.prompts.active_set is None
