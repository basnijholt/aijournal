from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from aijournal.common.app_config import AppConfig
    from aijournal.services.capture import NormalizeStageOutputs

    from .stage0_persist import EntryResult


def run_normalize_stage_1(
    entry_results: list[EntryResult],
    root: Path,
    config: AppConfig,
) -> NormalizeStageOutputs:
    from aijournal.services.capture import NormalizeStageOutputs, normalize_entries
    from aijournal.services.capture.results import OperationResult

    normalize_start = perf_counter()
    artifact_counts = normalize_entries(entry_results, root, config) if entry_results else {}
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
        {entry.date for entry in entry_results if entry.changed and not entry.deduped},
    )
    return NormalizeStageOutputs(artifact_counts, op_result, duration_ms, changed_dates)
