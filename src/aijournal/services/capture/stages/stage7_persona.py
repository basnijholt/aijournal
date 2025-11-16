from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from aijournal.api.capture import CaptureInput
    from aijournal.common.app_config import AppConfig
    from aijournal.services.capture import PersonaStage7Outputs


def run_persona_stage_7(
    inputs: CaptureInput,
    root: Path,
    config: AppConfig,
    artifacts_changed: dict[str, int],
) -> PersonaStage7Outputs:
    from aijournal.commands.persona import (
        persona_state,
        run_persona_build,
    )
    from aijournal.commands.profile import (
        load_profile_components,
        profile_to_dict,
    )
    from aijournal.services.capture import PersonaStage7Outputs
    from aijournal.services.capture.results import OperationResult
    from aijournal.services.capture.utils import relative_path

    stage_start = perf_counter()
    should_build = False
    persona_error: str | None = None
    persona_changed = False
    persona_stale_before = False
    persona_stale_after = False
    status_before = "unknown"
    status_after = "unknown"
    force_rebuild = inputs.rebuild == "always"
    try:
        status_before, _ = persona_state(root, root, config)
        persona_stale_before = status_before != "fresh"
        should_build = (
            force_rebuild or persona_stale_before or artifacts_changed.get("profile", 0) > 0
        )
        profile_model, claim_models = load_profile_components(root, config=config)
        profile_payload = profile_to_dict(profile_model)
        if should_build and (profile_payload or claim_models):
            _, persona_changed = run_persona_build(
                profile_payload,
                claim_models,
                config=config,
                root=root,
            )
        status_after, _ = persona_state(root, root, config)
        persona_stale_after = status_after != "fresh"
    except typer.Exit as exc:
        if exc.exit_code not in (0,):
            persona_error = str(exc)
    except Exception as exc:  # pragma: no cover - defensive
        persona_error = str(exc)
    duration_ms = (perf_counter() - stage_start) * 1000.0
    derived_dir = Path(config.paths.derived)
    if not derived_dir.is_absolute():
        derived_dir = root / derived_dir
    persona_core_path = derived_dir / "persona" / "persona_core.yaml"

    persona_artifacts: list[str] = []
    if persona_changed:
        persona_artifacts.append(relative_path(persona_core_path, root))
    persona_details: dict[str, object] = {
        "before_fresh": not persona_stale_before,
        "after_fresh": not persona_stale_after,
        "should_build": should_build,
        "mode": inputs.rebuild,
    }
    if persona_error is not None:
        op_result = OperationResult.fail(
            f"persona refresh failed: {persona_error}",
            details=persona_details,
        )
    elif persona_changed:
        op_result = OperationResult.wrote(
            persona_artifacts,
            message="persona rebuilt",
            details=persona_details,
        )
    elif persona_stale_after:
        op_result = OperationResult.noop(
            "persona remains stale",
            details=persona_details,
            warnings=["persona could not be refreshed"],
        )
    else:
        op_result = OperationResult.noop(
            "persona already fresh",
            details=persona_details,
        )
    return PersonaStage7Outputs(
        result=op_result,
        duration_ms=duration_ms,
        persona_changed=persona_changed,
        persona_stale_before=persona_stale_before,
        persona_stale_after=persona_stale_after,
        status_before=status_before,
        status_after=status_after,
        error=persona_error,
    )
