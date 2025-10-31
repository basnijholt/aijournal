"""Shared structured logging helpers."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aijournal.utils import time as time_utils

StructuredLogSink = Callable[[dict[str, Any]], None]


def _default_encoder(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, set):
        return sorted(value)
    return str(value)


@dataclass(slots=True)
class StructuredLogger:
    """Append-only NDJSON logger with optional live sinks."""

    path: Path
    base: Mapping[str, Any]
    sinks: Sequence[StructuredLogSink] = ()
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, **event: Any) -> None:
        payload: dict[str, Any] = {
            **self.base,
            **event,
        }
        payload.setdefault("timestamp", time_utils.format_timestamp(time_utils.now()))
        if self.enabled:
            line = json.dumps(payload, ensure_ascii=False, default=_default_encoder)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        for sink in self.sinks:
            sink(payload)

    @contextmanager
    def span(self, step: str, **fields: Any):
        start = time.perf_counter()
        self.emit(event="start", step=step, **fields)
        try:
            yield
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000.0
            self.emit(
                event="error",
                step=step,
                duration_ms=duration_ms,
                error=str(exc),
                **fields,
            )
            raise
        else:
            duration_ms = (time.perf_counter() - start) * 1000.0
            self.emit(
                event="end",
                step=step,
                duration_ms=duration_ms,
                **fields,
            )


def build_pretty_sink() -> StructuredLogSink:
    def _sink(payload: dict[str, Any]) -> None:
        command = payload.get("command")
        event = payload.get("event")
        step = payload.get("step")
        duration = payload.get("duration_ms")
        message = f"[{command}] {event}"
        if step:
            message += f" step={step}"
        if duration is not None:
            message += f" duration={duration:.1f}ms"
        error = payload.get("error")
        if error:
            message += f" error={error}"
        print(message)

    return _sink


def build_json_sink() -> StructuredLogSink:
    def _sink(payload: dict[str, Any]) -> None:
        print(json.dumps(payload, ensure_ascii=False, default=_default_encoder))

    return _sink
