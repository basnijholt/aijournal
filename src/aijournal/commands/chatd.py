"""Chat daemon command orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from pydantic import BaseModel

from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.config_loader import use_fake_llm
from aijournal.common.context import RunContext, create_run_context
from aijournal.services.chat_api import build_chat_app


class ChatdOptions(BaseModel):
    host: str
    port: int


@dataclass(slots=True)
class ChatdPrepared:
    host: str
    port: int


@dataclass(slots=True)
class ChatdResult:
    host: str
    port: int
    uvicorn: Any
    app_instance: Any


def prepare_inputs(ctx: RunContext, options: ChatdOptions) -> ChatdPrepared:
    if options.port <= 0 or options.port > 65535:
        typer.secho("--port must be between 1 and 65535.", fg=typer.colors.RED, err=True)
        ctx.emit(event="command_failed", reason="invalid_port")
        raise typer.Exit(1)
    ctx.emit(event="prepare_summary", host=options.host, port=options.port)
    return ChatdPrepared(host=options.host, port=options.port)


def invoke_pipeline(ctx: RunContext, prepared: ChatdPrepared) -> ChatdResult:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - optional dependency
        typer.secho(
            f"uvicorn is required for chatd: {exc}. Install with `uv add uvicorn fastapi`.",
            fg=typer.colors.RED,
            err=True,
        )
        ctx.emit(event="command_failed", reason="missing_uvicorn")
        raise typer.Exit(1)

    app_instance = build_chat_app(ctx.workspace, ctx.config)
    ctx.emit(event="pipeline_complete", host=prepared.host, port=prepared.port)
    return ChatdResult(
        host=prepared.host,
        port=prepared.port,
        uvicorn=uvicorn,
        app_instance=app_instance,
    )


def persist_output(ctx: RunContext, result: ChatdResult) -> None:
    del ctx
    typer.echo(f"chatd starting on http://{result.host}:{result.port}")
    result.uvicorn.run(
        result.app_instance,
        host=result.host,
        port=result.port,
        log_level="info",
    )


def run_chatd_command(ctx: RunContext, options: ChatdOptions) -> None:
    run_command_pipeline(
        ctx,
        options,
        prepare_inputs=prepare_inputs,
        invoke_pipeline=invoke_pipeline,
        persist_output=persist_output,
    )


def run_chatd(host: str, port: int) -> None:
    ctx = create_run_context(
        command="chatd",
        workspace=Path.cwd(),
        config={},
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )
    run_chatd_command(ctx, ChatdOptions(host=host, port=port))
