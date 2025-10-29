from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from .. import CaptureInput, PackStage8Outputs


def run_pack_stage_8(
    inputs: CaptureInput,
    root: Path,
    run_id: str,
    persona_changed: bool,
) -> PackStage8Outputs:
    from .. import OperationResult, PackStage8Outputs, _relative_path, run_pack

    if not inputs.pack:
        return PackStage8Outputs(OperationResult.noop("no pack requested"), 0.0)
    if not persona_changed:
        return PackStage8Outputs(
            OperationResult.noop("persona unchanged, pack not regenerated"),
            0.0,
        )

    stage_start = perf_counter()
    level = inputs.pack.upper()
    history_days = 1 if level == "L4" else 0
    pack_output = root / "derived" / "packs" / f"{level.lower()}_{run_id}.yaml"
    pack_error: str | None = None
    try:
        run_pack(
            level,
            None,
            output=pack_output,
            max_tokens=None,
            fmt="yaml",
            history_days=history_days,
            dry_run=False,
        )
    except typer.Exit as exc:
        if exc.exit_code not in (0,):
            pack_error = str(exc)
    except Exception as exc:  # pragma: no cover - defensive
        pack_error = str(exc)
    duration_ms = (perf_counter() - stage_start) * 1000.0
    pack_details: dict[str, object] = {"level": level, "history_days": history_days}
    if pack_error is not None:
        op_result = OperationResult.fail(
            f"pack generation failed: {pack_error}",
            details=pack_details,
        )
    else:
        rel_output = _relative_path(pack_output, root)
        op_result = OperationResult.wrote(
            [rel_output],
            message="pack generated",
            details=pack_details,
        )
    return PackStage8Outputs(op_result, duration_ms)
