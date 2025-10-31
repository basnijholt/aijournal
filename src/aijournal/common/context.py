"""Runtime context objects shared across command executors."""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aijournal.common.app_config import AppConfig
from aijournal.common.logging import (
    StructuredLogger,
    StructuredLogSink,
    build_json_sink,
    build_pretty_sink,
)
from aijournal.utils import time as time_utils


def _trace_path(root: Path) -> Path:
    return root / "derived" / "logs" / "run_trace.jsonl"


def _run_id(command: str) -> str:
    timestamp = time_utils.now().strftime("%Y%m%d%H%M%S")
    suffix = secrets.token_hex(2)
    slug = command.replace("/", "-").replace(" ", "-")
    return f"{slug}-{timestamp}-{suffix}"


@dataclass(slots=True)
class RunContext:
    command: str
    root: Path
    config: AppConfig
    use_fake_llm: bool
    logger: StructuredLogger
    trace_enabled: bool = False
    verbose_json: bool = False

    def emit(self, **event: Any) -> None:
        self.logger.emit(**event)

    def span(self, step: str, **fields: Any):
        return self.logger.span(step, **fields)


def create_run_context(
    *,
    command: str,
    root: Path,
    config: Mapping[str, Any] | AppConfig,
    use_fake_llm: bool,
    trace: bool,
    verbose_json: bool,
    sinks: Sequence[StructuredLogSink] | None = None,
) -> RunContext:
    run_id = _run_id(command)
    sink_list: list[StructuredLogSink] = list(sinks or [])
    if trace:
        sink_list.append(build_pretty_sink())
    if verbose_json:
        sink_list.append(build_json_sink())
    config_model = (
        config if isinstance(config, AppConfig) else AppConfig.model_validate(dict(config))
    )

    logger = StructuredLogger(
        path=_trace_path(root),
        base={"run_id": run_id, "command": command, "root": str(root)},
        sinks=sink_list,
        enabled=True,
    )
    return RunContext(
        command=command,
        root=root,
        config=config_model,
        use_fake_llm=use_fake_llm,
        logger=logger,
        trace_enabled=trace,
        verbose_json=verbose_json,
    )
