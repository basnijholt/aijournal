from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .. import EntryResult, NormalizeStageOutputs


def run_normalize_stage_1(
    entry_results: list[EntryResult],
    root: Path,
) -> NormalizeStageOutputs:
    from .. import NormalizeStageOutputs, OperationResult, normalize_entries

    normalize_start = perf_counter()
    artifact_counts = normalize_entries(entry_results, root) if entry_results else {}
    duration_ms = (perf_counter() - normalize_start) * 1000.0
    normalized_count = int(artifact_counts.get("normalized", 0))
    normalized_paths = artifact_counts.get("paths", [])
    normalize_details: dict[str, object] = {"normalized": normalized_count}
    if normalized_count:
        message = f"{normalized_count} normalized entries updated"
        op_result = OperationResult.wrote(
            normalized_paths,
            message=message,
            details=normalize_details,
        )
    else:
        op_result = OperationResult.noop(
            "normalized entries already up to date",
            details=normalize_details,
        )
    changed_dates = sorted(
        {entry.date for entry in entry_results if entry.changed and not entry.deduped}
    )
    return NormalizeStageOutputs(artifact_counts, op_result, duration_ms, changed_dates)
