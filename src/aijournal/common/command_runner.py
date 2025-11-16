"""Utility to run command pipelines with standardized logging."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel
from pydantic_core import PydanticSerializationError

if TYPE_CHECKING:
    from collections.abc import Callable

    from aijournal.common.context import RunContext

OptionsT = TypeVar("OptionsT", bound=BaseModel)
PreparedT = TypeVar("PreparedT")
ResultT = TypeVar("ResultT")
OutputT = TypeVar("OutputT")


def run_command_pipeline(
    ctx: RunContext,
    options: OptionsT,
    *,
    prepare_inputs: Callable[[RunContext, OptionsT], PreparedT],
    invoke_pipeline: Callable[[RunContext, PreparedT], ResultT],
    persist_output: Callable[[RunContext, ResultT], OutputT],
) -> OutputT:
    ctx.emit(event="command_start", options=_summarize(options))
    with ctx.span("prepare_inputs"):
        prepared = prepare_inputs(ctx, options)
    with ctx.span("invoke_pipeline"):
        result = invoke_pipeline(ctx, prepared)
    with ctx.span("persist_output"):
        output = persist_output(ctx, result)
    ctx.emit(event="command_complete", output=_summarize(output))
    return output


def _summarize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        try:
            return value.model_dump(exclude_none=True, mode="json")
        except PydanticSerializationError:
            raw = value.model_dump(exclude_none=True, mode="python")
            return _convert(raw)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [_summarize(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:  # pragma: no cover - defensive
            return str(value)
    return str(value)


def _convert(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {key: _convert(val) for key, val in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_convert(item) for item in obj]
    if callable(obj):
        return getattr(obj, "__name__", "callable")
    if isinstance(obj, Path):
        return str(obj)
    return obj
