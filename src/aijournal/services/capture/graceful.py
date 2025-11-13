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
            retries=retries,
            progress=progress,
            claim_models=claim_models,
            build_claim_preview=build_claim_preview,
            workspace=workspace,
        )
        return facts_path, None
    except typer.Exit as exc:
        if exc.exit_code == 0:
            return None, None
        return None, f"facts extraction exited with code {exc.exit_code}"
    except Exception as exc:
        return None, f"facts extraction failed: {exc}"


def graceful_profile_suggest(
    date: str,
    *,
    timeout: float,
    retries: int,
    progress: bool,
    workspace: Path,
) -> tuple[Path | None, str | None]:
    """Gracefully run profile suggestions, catching typer.Exit and returning None on failure.

    Returns:
        Tuple of (suggestions_path, error_message). If successful, error_message is None.
        If failed, suggestions_path is None and error_message contains the reason.
    """
    from aijournal.commands.profile import run_profile_suggest

    try:
        suggestions_path = run_profile_suggest(
            date,
            timeout=timeout,
            retries=retries,
            progress=progress,
            workspace=workspace,
        )
        return suggestions_path, None
    except typer.Exit as exc:
        if exc.exit_code == 0:
            return None, None
        return None, f"profile suggest exited with code {exc.exit_code}"
    except Exception as exc:
        return None, f"profile suggest failed: {exc}"


def graceful_profile_apply(
    date: str,
    *,
    suggestions_path: Path,
    auto_confirm: bool,
    workspace: Path,
) -> tuple[bool, str | None]:
    """Gracefully run profile apply, catching typer.Exit and returning status.

    Returns:
        Tuple of (success, error_message). If successful, error_message is None.
        If failed, success is False and error_message contains the reason.
    """
    from aijournal.commands.profile import run_profile_apply

    try:
        run_profile_apply(
            date,
            suggestions_path=suggestions_path,
            auto_confirm=auto_confirm,
            workspace=workspace,
        )
        return True, None
    except typer.Exit as exc:
        if exc.exit_code == 0:
            # Exit 0 means no changes to apply, which is fine
            return True, None
        return False, f"profile apply exited with code {exc.exit_code}"
    except Exception as exc:
        return False, f"profile apply failed: {exc}"


def graceful_characterize(
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
    """Gracefully run characterize, catching typer.Exit and returning None on failure.

    Returns:
        Tuple of (batch_path, error_message). If successful, error_message is None.
        If failed, batch_path is None and error_message contains the reason.
    """
    from aijournal.commands.characterize import run_characterize

    try:
        batch_path = run_characterize(
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
        return None, f"characterize exited with code {exc.exit_code}"
    except Exception as exc:
        return None, f"characterize failed: {exc}"
