"""Runtime context objects shared across command executors."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aijournal.common.app_config import AppConfig
from aijournal.common.config_loader import resolve_prompt_set
from aijournal.common.logging import (
    StructuredLogger,
    StructuredLogSink,
    build_json_sink,
    build_pretty_sink,
)
from aijournal.utils import time as time_utils

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def _trace_path(workspace: Path, config: AppConfig) -> Path:
    """Build trace log path from workspace and config."""
    derived = Path(config.paths.derived)
    if not derived.is_absolute():
        derived = workspace / derived
    return derived / "logs" / "run_trace.jsonl"


def _run_id(command: str) -> str:
    timestamp = time_utils.now().strftime("%Y%m%d%H%M%S")
    suffix = secrets.token_hex(2)
    slug = command.replace("/", "-").replace(" ", "-")
    return f"{slug}-{timestamp}-{suffix}"


@dataclass(slots=True)
class RunContext:
    command: str
    workspace: Path
    config: AppConfig
    use_fake_llm: bool
    logger: StructuredLogger
    trace_enabled: bool = False
    verbose_json: bool = False
    prompt_set: str | None = None

    def emit(self, **event: Any) -> None:
        self.logger.emit(**event)

    def span(self, step: str, **fields: Any):
        return self.logger.span(step, **fields)


def create_run_context(
    *,
    command: str,
    workspace: Path,
    config: Mapping[str, Any] | AppConfig,
    use_fake_llm: bool,
    trace: bool,
    verbose_json: bool,
    sinks: Sequence[StructuredLogSink] | None = None,
    prompt_set: str | None = None,
) -> RunContext:
    """Create a runtime context for command execution.

    Args:
        command: Command name being executed
        workspace: Workspace directory containing config.yaml
        config: Configuration (dict or AppConfig instance)
        use_fake_llm: Whether to use fake LLM for testing
        trace: Whether to enable trace logging
        verbose_json: Whether to enable verbose JSON logging
        sinks: Optional additional log sinks

    Returns:
        Configured RunContext

    """
    # Normalize config to model
    config_model = (
        config if isinstance(config, AppConfig) else AppConfig.model_validate(dict(config))
    )

    resolved_prompt_set = resolve_prompt_set(cli_override=prompt_set, config=config_model)

    run_id = _run_id(command)
    sink_list: list[StructuredLogSink] = list(sinks or [])
    if trace:
        sink_list.append(build_pretty_sink())
    if verbose_json:
        sink_list.append(build_json_sink())

    logger = StructuredLogger(
        path=_trace_path(workspace, config_model),
        base={
            "run_id": run_id,
            "command": command,
            "workspace": str(workspace),
            "prompt_set": resolved_prompt_set,
        },
        sinks=sink_list,
        enabled=True,
    )
    return RunContext(
        command=command,
        workspace=workspace,
        config=config_model,
        use_fake_llm=use_fake_llm,
        logger=logger,
        trace_enabled=trace,
        verbose_json=verbose_json,
        prompt_set=resolved_prompt_set,
    )
