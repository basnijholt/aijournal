"""Shared helpers for CLI option validation and file handling."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

import typer


def normalize_choice(
    value: str,
    *,
    option: str,
    allowed: Mapping[str, str] | Sequence[str],
    casefold_input: bool = True,
    error: Literal["badparam", "exit"] = "badparam",
    exit_code: int = 2,
) -> str:
    """Normalize option values and emit consistent error messages."""

    if isinstance(allowed, Mapping):
        lookup = dict(allowed)
        display_values = list(dict.fromkeys(allowed.values()))
        casefold_lookup = casefold_input
    else:
        lookup = {(item.lower() if casefold_input else item): item for item in allowed}
        display_values = list(dict.fromkeys(lookup.values()))
        casefold_lookup = casefold_input

    normalized_key = value.strip()
    if casefold_lookup:
        normalized_key = normalized_key.lower()

    canonical = lookup.get(normalized_key)
    if canonical is None:
        message = f"{option} must be one of: {', '.join(display_values)}."
        if error == "exit":
            typer.secho(message, fg=typer.colors.RED, err=True)
            raise typer.Exit(exit_code)
        raise typer.BadParameter(message, param_hint=option)

    return canonical


def resolve_workspace_path(workspace: Path, target: Path) -> Path:
    """Resolve a possibly relative path against the workspace root."""

    return target if target.is_absolute() else workspace / target


def ensure_output_write(path: Path, *, overwrite: bool, exit_code: int = 1) -> None:
    """Abort when attempting to clobber existing files unless overwrite is allowed."""

    if path.exists() and not overwrite:
        typer.secho(
            f"Refusing to overwrite existing file: {path}. Use --overwrite to replace it.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(exit_code)


def write_text_file(path: Path, text: str) -> None:
    """Ensure parent directories exist and persist text."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
