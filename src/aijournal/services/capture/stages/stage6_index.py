from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING, Literal

import typer

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from aijournal.services.capture import IndexStage6Outputs


def run_index_stage_6(
    changed_dates: Sequence[str],
    root: Path,
    rebuild_mode: Literal["auto", "always", "skip"] = "auto",
) -> IndexStage6Outputs:
    from aijournal.commands.index import run_index_rebuild, run_index_tail
    from aijournal.services.capture import IndexStage6Outputs
    from aijournal.services.capture.results import OperationResult
    from aijournal.services.capture.utils import relative_path

    stage_start = perf_counter()
    index_message = ""
    index_error: str | None = None
    index_updated = False
    rebuilt = False
    force_rebuild = rebuild_mode == "always"
    changed_dates_list = list(changed_dates)
    try:
        chroma_dir = root / "derived" / "index" / "chroma"
        if force_rebuild or not chroma_dir.exists():
            index_message = run_index_rebuild(since=None, limit=None)
            rebuilt = True
            index_updated = True
        elif changed_dates_list:
            since = min(changed_dates_list)
            index_message = run_index_tail(since=since, days=7, limit=None)
            if not index_message or "already up to date" not in index_message.lower():
                index_updated = True
        else:
            index_message = "no capture changes detected"
    except typer.Exit as exc:
        if exc.exit_code not in (0,):
            index_error = str(exc)
    except Exception as exc:  # pragma: no cover - defensive
        index_error = str(exc)
    duration_ms = (perf_counter() - stage_start) * 1000.0
    index_details: dict[str, object] = {
        "message": index_message,
        "rebuild": rebuilt,
        "mode": rebuild_mode,
    }
    if index_error is not None:
        op_result = OperationResult.fail(
            f"index update failed: {index_error}",
            details=index_details,
        )
    elif index_updated:
        index_artifacts = [
            relative_path(root / "derived" / "index" / "chroma", root),
            relative_path(root / "derived" / "index" / "meta.json", root),
        ]
        op_result = OperationResult.wrote(
            index_artifacts,
            message=index_message or "index refreshed",
            details=index_details,
        )
    else:
        op_result = OperationResult.noop(
            index_message or "index already up to date",
            details=index_details,
        )
    return IndexStage6Outputs(op_result, duration_ms, index_updated, rebuilt)
