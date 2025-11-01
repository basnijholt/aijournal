from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from aijournal.common.app_config import AppConfig

    from .. import CaptureInput, ProfileStage4Outputs


def run_profile_stage_4(
    changed_dates: list[str],
    inputs: CaptureInput,
    root: Path,
    config: AppConfig,
) -> ProfileStage4Outputs:
    from aijournal.commands.profile import run_profile_apply, run_profile_suggest
    from aijournal.common.constants import DEFAULT_TIMEOUT_SECONDS

    from .. import OperationResult, ProfileStage4Outputs
    from ..utils import relative_path

    del config  # Stage loads config internally via workspace

    stage_start = perf_counter()
    suggestion_paths: list[str] = []
    suggestion_errors: list[str] = []
    apply_errors: list[str] = []
    applied_count = 0
    for date in changed_dates:
        suggestions_path: Path | None = None
        try:
            suggestions_path = run_profile_suggest(
                date,
                timeout=DEFAULT_TIMEOUT_SECONDS,
                retries=inputs.retries,
                progress=inputs.progress,
                workspace=root,
            )
        except typer.Exit as exc:
            if exc.exit_code not in (0,):
                suggestion_errors.append(f"{date}: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            suggestion_errors.append(f"{date}: {exc}")
        else:
            if suggestions_path is not None:
                suggestion_paths.append(relative_path(suggestions_path, root))

        if inputs.apply_profile == "auto" and suggestions_path is not None:
            try:
                run_profile_apply(
                    date,
                    suggestions_path=suggestions_path,
                    auto_confirm=True,
                    workspace=root,
                )
            except typer.Exit as exc:
                if exc.exit_code not in (0,):
                    apply_errors.append(f"{date}: {exc}")
            except Exception as exc:  # pragma: no cover - defensive
                apply_errors.append(f"{date}: {exc}")
            else:
                applied_count += 1

    duration_ms = (perf_counter() - stage_start) * 1000.0
    suggestion_details: dict[str, object] = {
        "dates": changed_dates,
        "apply_mode": inputs.apply_profile,
        "suggestions": suggestion_paths,
    }
    if suggestion_errors:
        message = (
            "profile suggestions completed with errors"
            if suggestion_paths
            else "profile suggestion stage failed"
        )
        suggest_result = OperationResult(
            ok=bool(suggestion_paths),
            changed=bool(suggestion_paths),
            message=message,
            artifacts=suggestion_paths,
            warnings=suggestion_errors,
            details=suggestion_details,
        )
    elif suggestion_paths:
        suggest_result = OperationResult.wrote(
            suggestion_paths,
            message="profile suggestions generated",
            details=suggestion_details,
        )
    else:
        suggest_result = OperationResult.noop(
            "profile suggestions already up to date",
            details=suggestion_details,
        )

    apply_result: OperationResult | None = None
    if inputs.apply_profile == "auto":
        apply_details: dict[str, object] = {
            "dates": changed_dates,
            "applied": applied_count,
        }
        if apply_errors:
            message = (
                "profile updates applied with errors"
                if applied_count
                else "profile apply stage failed"
            )
            apply_result = OperationResult(
                ok=bool(applied_count),
                changed=bool(applied_count),
                message=message,
                warnings=apply_errors,
                details=apply_details,
            )
        elif applied_count:
            apply_result = OperationResult(
                ok=True,
                changed=True,
                message="profile updates applied",
                details=apply_details,
            )
        else:
            apply_result = OperationResult.noop(
                "no profile updates required",
                details=apply_details,
            )

    return ProfileStage4Outputs(
        suggest_result=suggest_result,
        apply_result=apply_result,
        duration_ms=duration_ms,
        suggestion_paths=suggestion_paths,
        applied_count=applied_count,
    )
