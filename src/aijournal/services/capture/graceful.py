"""Graceful wrappers for command operations that can fail.

This module provides wrapper functions that catch typer.Exit exceptions
and convert them into structured OperationResult objects with warnings,
ensuring the capture orchestrator always receives a result object.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import typer

if TYPE_CHECKING:
    from aijournal.domain.changes import ClaimProposal
    from aijournal.domain.claims import ClaimAtom
    from aijournal.models.derived import ProfileUpdatePreview


T = TypeVar("T")


def graceful_summarize(
    date: str,
    *,
    timeout: float,
    retries: int,
    progress: bool,
    workspace: Path,
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
            timeout=timeout,
            retries=retries,
            progress=progress,
            workspace=workspace,
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
    timeout: float,
    retries: int,
    progress: bool,
    claim_models: Sequence[ClaimAtom],
    build_claim_preview: Callable[
        [Sequence[ClaimProposal], Sequence[ClaimAtom], str], ProfileUpdatePreview | None
    ],
    workspace: Path,
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
            timeout=timeout,
            progress=progress,
            claim_models=claim_models,
            build_claim_preview=build_claim_preview,
            workspace=workspace,
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
    timeout: float,
    retries: int,
    progress: bool,
    build_claim_preview: Callable[
        [Sequence[ClaimProposal], Sequence[ClaimAtom], str], ProfileUpdatePreview | None
    ],
    workspace: Path,
) -> tuple[Path | None, str | None]:
    """Gracefully run the unified profile update pipeline."""

    from aijournal.commands.profile_update import run_profile_update

    try:
        batch_path = run_profile_update(
            date,
            timeout=timeout,
            retries=retries,
            progress=progress,
            build_claim_preview=build_claim_preview,
            workspace=workspace,
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
