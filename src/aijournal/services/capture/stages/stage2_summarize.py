from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .. import CaptureInput, SummarizeStage2Outputs


def run_summarize_stage_2(
    changed_dates: list[str],
    inputs: CaptureInput,
    root: Path,
) -> SummarizeStage2Outputs:
    from aijournal.common.constants import DEFAULT_TIMEOUT_SECONDS

    from .. import (
        OperationResult,
        SummarizeStage2Outputs,
    )
    from ..graceful import graceful_summarize
    from ..utils import relative_path

    stage_start = perf_counter()
    summary_paths: list[str] = []
    summary_errors: list[str] = []
    for date in changed_dates:
        summary_path, error = graceful_summarize(
            date,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            retries=inputs.retries,
            progress=inputs.progress,
            workspace=root,
        )
        if error:
            summary_errors.append(f"{date}: {error}")
        elif summary_path:
            summary_paths.append(relative_path(summary_path, root))
    duration_ms = (perf_counter() - stage_start) * 1000.0
    summary_details: dict[str, object] = {"dates": changed_dates}
    if summary_errors:
        message = "summaries completed with errors" if summary_paths else "summaries failed"
        op_result = OperationResult(
            ok=bool(summary_paths),
            changed=bool(summary_paths),
            message=message,
            artifacts=summary_paths,
            warnings=summary_errors,
            details=summary_details,
        )
    elif summary_paths:
        message = f"generated summaries for {len(summary_paths)} entries"
        op_result = OperationResult.wrote(
            summary_paths,
            message=message,
            details=summary_details,
        )
    else:
        op_result = OperationResult.noop(
            "summaries already up to date",
            details=summary_details,
        )
    return SummarizeStage2Outputs(op_result, duration_ms, summary_paths)
