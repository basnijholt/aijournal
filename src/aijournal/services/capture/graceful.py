"""Graceful wrappers for command operations that can fail.

This module provides wrapper functions that catch typer.Exit exceptions
and convert them into structured OperationResult objects with warnings,
ensuring the capture orchestrator always receives a result object.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

import typer

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from aijournal.common.app_config import AppConfig
    from aijournal.domain.claims import ClaimAtom


T = TypeVar("T")


def graceful_summarize(
    date: str,
    *,
    progress: bool,
    workspace: Path,
    config: AppConfig | None = None,
) -> tuple[Path | None, str | None]:
    """Gracefully run summarize, catching typer.Exit and returning None on failure.

    Returns:
        Tuple of (summary_path, error_message). If successful, error_message is None.
        If failed, summary_path is None and error_message contains the reason.

    """
    from aijournal.commands.summarize import run_summarize

    try:
        summary_path = run_summarize(
            date,
            progress=progress,
            workspace=workspace,
            config=config,
        )
        return summary_path, None
    except typer.Exit as exc:
        if exc.exit_code == 0:
            # Exit code 0 is success, shouldn't happen but handle it
            return None, None
        # Try to extract the original error from the exception chain
        if exc.__cause__ is not None:
            return None, f"summarize failed: {exc.__cause__}"
        return None, f"summarize exited with code {exc.exit_code}"
    except Exception as exc:
        return None, f"summarize failed: {exc}"


def graceful_facts(
    date: str,
    *,
    progress: bool,
    claim_models: Sequence[ClaimAtom],
    generate_preview: bool,
    workspace: Path,
    config: AppConfig | None = None,
) -> tuple[Path | None, str | None]:
    """Gracefully run facts extraction, catching typer.Exit and returning None on failure.

    Returns:
        Tuple of (facts_path, error_message). If successful, error_message is None.
        If failed, facts_path is None and error_message contains the reason.

    """
    from aijournal.commands.facts import run_facts

    try:
        _, facts_path = run_facts(
            date,
            progress=progress,
            claim_models=claim_models,
            generate_preview=generate_preview,
            workspace=workspace,
            config=config,
        )
        return facts_path, None
    except typer.Exit as exc:
        if exc.exit_code == 0:
            return None, None
        # Try to extract the original error from the exception chain
        if exc.__cause__ is not None:
            return None, f"facts extraction failed: {exc.__cause__}"
        return None, f"facts extraction exited with code {exc.exit_code}"
    except Exception as exc:
        return None, f"facts extraction failed: {exc}"


def graceful_profile_update(
    date: str,
    *,
    progress: bool,
    generate_preview: bool,
    workspace: Path,
    config: AppConfig | None = None,
) -> tuple[Path | None, str | None]:
    """Gracefully run the unified profile update pipeline."""
    from aijournal.commands.profile_update import run_profile_update

    try:
        batch_path = run_profile_update(
            date,
            progress=progress,
            generate_preview=generate_preview,
            workspace=workspace,
            config=config,
        )
        return batch_path, None
    except typer.Exit as exc:
        if exc.exit_code == 0:
            return None, None
        if exc.__cause__ is not None:
            return None, f"profile update failed: {exc.__cause__}"
        return None, f"profile update exited with code {exc.exit_code}"
    except Exception as exc:
        return None, f"profile update failed: {exc}"
