from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from aijournal.api.capture import CaptureInput
    from aijournal.common.app_config import AppConfig
    from aijournal.services.capture import ProfileUpdateStageOutputs


def run_profile_update_stage(
    changed_dates: list[str],
    inputs: CaptureInput,
    root: Path,
    config: AppConfig,
) -> ProfileUpdateStageOutputs:
    from aijournal.services.capture import ProfileUpdateStageOutputs
    from aijournal.services.capture.graceful import graceful_profile_update
    from aijournal.services.capture.results import OperationResult
    from aijournal.services.capture.utils import (
        apply_profile_update_batch,
        pending_batches,
        relative_path,
    )

    stage_start = perf_counter()
    batch_paths: list[str] = []
    run_errors: list[str] = []
    review_errors: list[str] = []
    applied_batches: list[str] = []
    pending_batches_rel: list[str] = []
    review_candidates: list[str] = []

    for date in changed_dates:
        before = pending_batches(root, config)
        batch_path, error = graceful_profile_update(
            date,
            progress=inputs.progress,
            generate_preview=False,
            workspace=root,
            config=config,
        )
        if error:
            run_errors.append(f"{date}: {error}")
            continue
        if batch_path is None:
            continue

        rel_batch = relative_path(batch_path, root)
        batch_paths.append(rel_batch)

        after = pending_batches(root, config)
        new_batches = sorted(after - before)
        if batch_path not in new_batches:
            new_batches.append(batch_path)

        for pending_path in new_batches:
            review_candidates.append(relative_path(pending_path, root))

        if inputs.apply_profile == "auto":
            for pending_path in new_batches:
                rel_path = relative_path(pending_path, root)
                try:
                    if apply_profile_update_batch(root, config, pending_path):
                        applied_batches.append(rel_path)
                    else:
                        pending_batches_rel.append(rel_path)
                except Exception as exc:  # pragma: no cover - defensive
                    review_errors.append(f"{rel_path}: {exc}")
        else:
            pending_batches_rel.extend(relative_path(path, root) for path in new_batches)

    duration_ms = (perf_counter() - stage_start) * 1000.0

    profile_update_details: dict[str, object] = {
        "dates": changed_dates,
        "new_batches": batch_paths,
        "apply_mode": inputs.apply_profile,
    }
    if run_errors:
        message = (
            "profile update completed with errors"
            if batch_paths or applied_batches
            else "profile update stage failed"
        )
        update_result = OperationResult(
            ok=bool(batch_paths or applied_batches),
            changed=bool(batch_paths or applied_batches),
            message=message,
            artifacts=batch_paths,
            warnings=run_errors,
            details=profile_update_details,
        )
    elif batch_paths or applied_batches:
        update_result = OperationResult.wrote(
            batch_paths,
            message="profile updates generated",
            details=profile_update_details,
        )
    else:
        update_result = OperationResult.noop(
            "no profile updates required",
            details=profile_update_details,
        )

    review_result: OperationResult | None = None
    if inputs.apply_profile == "auto":
        review_details: dict[str, object] = {
            "apply_mode": inputs.apply_profile,
            "applied_batches": applied_batches,
            "pending_batches": pending_batches_rel,
        }
        if review_errors:
            message = (
                "profile batches applied with errors"
                if applied_batches
                else "profile review stage failed"
            )
            review_result = OperationResult(
                ok=bool(applied_batches),
                changed=bool(applied_batches),
                message=message,
                artifacts=applied_batches,
                warnings=review_errors,
                details=review_details,
            )
        elif applied_batches:
            review_result = OperationResult.wrote(
                applied_batches,
                message="profile batches applied",
                details=review_details,
            )
        elif pending_batches_rel:
            review_result = OperationResult.noop(
                "profile batches pending manual review",
                details=review_details,
            )
        else:
            review_result = OperationResult.noop(
                "no profile batches generated",
                details=review_details,
            )

    return ProfileUpdateStageOutputs(
        result=update_result,
        review_result=review_result,
        duration_ms=duration_ms,
        new_batches=batch_paths,
        applied_batches=applied_batches,
        pending_batches=pending_batches_rel,
        review_candidates=review_candidates,
    )
