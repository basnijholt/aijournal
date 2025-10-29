from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from .. import IndexStage6Outputs


def _run_index_stage_6(
    changed_dates: list[str],
    root: Path,
) -> IndexStage6Outputs:
    from .. import (
        IndexStage6Outputs,
        OperationResult,
        _relative_path,
        run_index_rebuild,
        run_index_tail,
    )

    stage_start = perf_counter()
    index_message = ""
    index_error: str | None = None
    index_updated = False
    rebuilt = False
    try:
        index_db = root / "derived" / "index" / "index.db"
        if not index_db.exists():
            index_message = run_index_rebuild(since=None, limit=None)
            rebuilt = True
            index_updated = True
        else:
            since = min(changed_dates)
            index_message = run_index_tail(since=since, days=7, limit=None)
            if not index_message or "already up to date" not in index_message.lower():
                index_updated = True
    except typer.Exit as exc:
        if exc.exit_code not in (0,):
            index_error = str(exc)
    except Exception as exc:  # pragma: no cover - defensive
        index_error = str(exc)
    duration_ms = (perf_counter() - stage_start) * 1000.0
    index_details: dict[str, object] = {
        "message": index_message,
        "rebuild": rebuilt,
    }
    if index_error is not None:
        op_result = OperationResult.fail(
            f"index update failed: {index_error}",
            details=index_details,
        )
    elif index_updated:
        index_artifacts = [
            _relative_path(root / "derived" / "index" / "index.db", root),
            _relative_path(root / "derived" / "index" / "annoy.index", root),
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
