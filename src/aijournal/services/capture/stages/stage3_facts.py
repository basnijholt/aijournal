from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from aijournal.api.capture import CaptureInput
    from aijournal.common.app_config import AppConfig
    from aijournal.services.capture import FactsStage3Outputs


def run_facts_stage_3(
    changed_dates: list[str],
    inputs: CaptureInput,
    root: Path,
    config: AppConfig,
) -> FactsStage3Outputs:
    from aijournal.commands.profile import load_profile_components
    from aijournal.services.capture import FactsStage3Outputs
    from aijournal.services.capture.graceful import graceful_facts
    from aijournal.services.capture.results import OperationResult
    from aijournal.services.capture.utils import relative_path

    stage_start = perf_counter()
    facts_paths: list[str] = []
    facts_errors: list[str] = []
    _, claim_models = load_profile_components(root, config=config)
    for date in changed_dates:
        facts_path, error = graceful_facts(
            date,
            progress=inputs.progress,
            claim_models=claim_models,
            generate_preview=False,
            workspace=root,
            config=config,
        )
        if error:
            facts_errors.append(f"{date}: {error}")
        elif facts_path:
            facts_paths.append(relative_path(facts_path, root))
    duration_ms = (perf_counter() - stage_start) * 1000.0
    facts_details: dict[str, object] = {"dates": changed_dates}
    if facts_errors:
        message = "facts completed with errors" if facts_paths else "facts failed"
        op_result = OperationResult(
            ok=bool(facts_paths),
            changed=bool(facts_paths),
            message=message,
            artifacts=facts_paths,
            warnings=facts_errors,
            details=facts_details,
        )
    elif facts_paths:
        message = f"extracted micro-facts for {len(facts_paths)} entries"
        op_result = OperationResult.wrote(
            facts_paths,
            message=message,
            details=facts_details,
        )
    else:
        op_result = OperationResult.noop(
            "micro-facts already up to date",
            details=facts_details,
        )
    return FactsStage3Outputs(op_result, duration_ms, facts_paths)
