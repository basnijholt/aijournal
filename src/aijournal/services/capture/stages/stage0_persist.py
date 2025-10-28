from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aijournal.models import ManifestEntry

    from .. import CaptureInput, PersistStage0Outputs


def run_persist_stage_0(
    inputs: CaptureInput,
    root: Path,
    manifest_entries: list[ManifestEntry],
    log_event: Callable[[dict[str, object]], None],
) -> PersistStage0Outputs:
    from .. import (
        EntryResult,
        OperationResult,
        PersistStage0Outputs,
        _discover_markdown_files,
        _ensure_manifest,
        _manifest_index,
        _persist_file_entry,
        _persist_text_entry,
    )

    entry_results: list[EntryResult] = []
    stage_entry_warnings: list[str] = []

    persist_start = perf_counter()
    if inputs.source in {"stdin", "editor"}:
        if not inputs.text:
            msg = "capture text input requires non-empty text"
            log_event({"event": "persist", "status": "error", "error": msg})
            raise ValueError(msg)
        entry = _persist_text_entry(inputs, root, manifest_entries)
        stage_entry_warnings.extend(entry.warnings)
        entry_results.append(entry)
    else:
        if not inputs.paths:
            msg = "capture --from requires at least one path"
            log_event({"event": "persist", "status": "error", "error": msg})
            raise ValueError(msg)
        files = _discover_markdown_files(inputs.paths)
        if not files:
            msg = "capture --from found no Markdown files"
            log_event({"event": "persist", "status": "error", "error": msg})
            raise ValueError(msg)
        _ensure_manifest(manifest_entries, root)
        manifest_idx = _manifest_index(manifest_entries)
        for file_path in files:
            entry = _persist_file_entry(
                inputs,
                root,
                manifest_entries,
                source_path=file_path,
                snapshot=inputs.snapshot,
                manifest_index=manifest_idx,
            )
            stage_entry_warnings.extend(entry.warnings)
            entry_results.append(entry)

    duration_ms = (perf_counter() - persist_start) * 1000.0
    created_count = sum(1 for entry in entry_results if entry.changed and not entry.deduped)
    deduped_count = sum(1 for entry in entry_results if entry.deduped)
    artifacts = [
        entry.markdown_path
        for entry in entry_results
        if entry.changed and not entry.deduped and entry.markdown_path
    ]
    persist_details: dict[str, object] = {
        "entries": len(entry_results),
        "created": created_count,
        "deduped": deduped_count,
    }
    message = f"{created_count} entries persisted" if created_count else "no new entries persisted"
    op_result = OperationResult.wrote(
        artifacts,
        message=message,
        warnings=stage_entry_warnings,
        details=persist_details,
    )
    return PersistStage0Outputs(entry_results, op_result, duration_ms)
