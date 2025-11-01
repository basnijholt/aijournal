from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from .. import CaptureInput, CharacterizeStage5Outputs


def run_characterize_stage_5(
    changed_dates: list[str],
    inputs: CaptureInput,
    root: Path,
) -> CharacterizeStage5Outputs:
    from aijournal.commands.characterize import run_characterize

    from .. import DEFAULT_TIMEOUT_SECONDS, CharacterizeStage5Outputs, OperationResult
    from ..utils import (
        apply_profile_update_batch,
        noop_preview,
        pending_batches,
        relative_path,
    )

    stage_start = perf_counter()
    characterize_paths: list[str] = []
    characterize_errors: list[str] = []
    review_applied: list[str] = []
    review_pending: list[str] = []
    review_candidates: list[str] = []
    review_errors: list[str] = []
    for date in changed_dates:
        pending_before = pending_batches()
        try:
            batch_path = run_characterize(
                date,
                timeout=DEFAULT_TIMEOUT_SECONDS,
                retries=inputs.retries,
                progress=inputs.progress,
                build_claim_preview=noop_preview,
            )
        except typer.Exit as exc:
            if exc.exit_code not in (0,):
                characterize_errors.append(f"{date}: {exc}")
            continue
        except Exception as exc:  # pragma: no cover - defensive
            characterize_errors.append(f"{date}: {exc}")
            continue
        else:
            rel_batch = relative_path(batch_path, root)
            characterize_paths.append(rel_batch)

        pending_after = pending_batches()
        new_batches = sorted(pending_after - pending_before)
        if batch_path not in new_batches:
            new_batches.append(batch_path)

        for pending_path in new_batches:
            rel_pending = relative_path(pending_path, root)
            review_candidates.append(rel_pending)

        if inputs.apply_profile == "auto":
            for pending_path in new_batches:
                try:
                    if apply_profile_update_batch(pending_path):
                        review_applied.append(relative_path(pending_path, root))
                    else:
                        review_pending.append(relative_path(pending_path, root))
                except Exception as exc:  # pragma: no cover - defensive
                    review_errors.append(f"{relative_path(pending_path, root)}: {exc}")
        else:
            review_pending.extend(relative_path(path, root) for path in new_batches)

    duration_ms = (perf_counter() - stage_start) * 1000.0
    characterize_details: dict[str, object] = {
        "dates": changed_dates,
        "new_batches": characterize_paths,
        "apply_mode": inputs.apply_profile,
    }
    if characterize_errors:
        message = (
            "characterize/review completed with errors"
            if characterize_paths or review_applied
            else "characterize stage failed"
        )
        characterize_result = OperationResult(
            ok=bool(characterize_paths or review_applied),
            changed=bool(characterize_paths or review_applied),
            message=message,
            artifacts=characterize_paths,
            warnings=characterize_errors,
            details=characterize_details,
        )
    elif characterize_paths or review_applied:
        characterize_result = OperationResult.wrote(
            characterize_paths,
            message="characterization batches generated",
            details=characterize_details,
        )
    else:
        characterize_result = OperationResult.noop(
            "no characterization updates needed",
            details=characterize_details,
        )

    review_result: OperationResult | None = None
    if inputs.apply_profile == "auto":
        review_details: dict[str, object] = {
            "apply_mode": inputs.apply_profile,
            "applied_batches": review_applied,
            "pending_batches": review_pending,
        }
        if review_errors:
            message = (
                "profile batches applied with errors"
                if review_applied
                else "profile review stage failed"
            )
            review_result = OperationResult(
                ok=bool(review_applied),
                changed=bool(review_applied),
                message=message,
                artifacts=review_applied,
                warnings=review_errors,
                details=review_details,
            )
        elif review_applied:
            review_result = OperationResult.wrote(
                review_applied,
                message="profile batches applied",
                details=review_details,
            )
        elif review_pending:
            review_result = OperationResult.noop(
                "profile batches pending manual review",
                details=review_details,
            )
        else:
            review_result = OperationResult.noop(
                "no profile batches generated",
                details=review_details,
            )

    return CharacterizeStage5Outputs(
        result=characterize_result,
        review_result=review_result,
        duration_ms=duration_ms,
        new_batches=characterize_paths,
        applied_batches=review_applied,
        pending_batches=review_pending,
        review_candidates=review_candidates,
    )
