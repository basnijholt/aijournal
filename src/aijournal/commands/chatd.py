"""Chat daemon command orchestration."""

from __future__ import annotations

from pathlib import Path

import typer

from aijournal.commands.ingest import _load_config
from aijournal.services import build_chat_app


def run_chatd(host: str, port: int) -> None:
    """Start the HTTP chat daemon."""
    if port <= 0 or port > 65535:
        typer.secho("--port must be between 1 and 65535.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - optional dependency
        typer.secho(
            f"uvicorn is required for chatd: {exc}. Install with `uv add uvicorn fastapi`.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    root = Path.cwd()
    config = _load_config(root)
    app_instance = build_chat_app(root, config)
    typer.echo(f"chatd starting on http://{host}:{port}")
    uvicorn.run(app_instance, host=host, port=port, log_level="info")
