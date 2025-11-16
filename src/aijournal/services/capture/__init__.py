"""Schemas and entry point for the capture orchestrator (Phase 2 scaffold)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from pydantic import BaseModel, Field

from aijournal.api.capture import CaptureInput  # noqa: TC001
from aijournal.common.config_loader import load_config_with_overrides
from aijournal.common.logging import StructuredLogger
from aijournal.services.capture.results import OperationResult, StageResult
from aijournal.services.capture.utils import normalize_markdown
from aijournal.services.ollama import build_ollama_config_from_mapping
from aijournal.utils import time as time_utils

from .stages.stage0_persist import EntryResult, run_persist_stage_0
from .stages.stage1_normalize import run_normalize_stage_1
from .stages.stage2_summarize import run_summarize_stage_2
from .stages.stage3_facts import run_facts_stage_3
from .stages.stage4_profile_update import run_profile_update_stage
from .stages.stage6_index import run_index_stage_6
from .stages.stage7_persona import run_persona_stage_7
from .stages.stage8_pack import run_pack_stage_8
from .utils import (
    digest_bytes,
    emit_operation_event,
    relative_path,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from aijournal.common.app_config import AppConfig
    from aijournal.models.authoritative import ManifestEntry


class CaptureStage(NamedTuple):
    stage_id: int
    name: str
    description: str
    manual: str


CAPTURE_STAGES: list[CaptureStage] = [
    CaptureStage(
        0,
        "persist",
        "Write canonical Markdown, update manifest, and store optional raw snapshots.",
        "Handled automatically by capture (no standalone command).",
    ),
    CaptureStage(
        1,
        "normalize",
        "Emit normalized YAML for new or changed entries.",
        "uv run aijournal ops pipeline normalize data/journal/YYYY/MM/DD/<entry>.md",
    ),
    CaptureStage(
        2,
        "summarize",
        "Generate daily summaries for affected dates.",
        "uv run aijournal ops pipeline summarize --date YYYY-MM-DD",
    ),
    CaptureStage(
        3,
        "extract_facts",
        "Derive micro-facts and claim proposals for affected dates.",
        "uv run aijournal ops pipeline extract-facts --date YYYY-MM-DD",
    ),
    CaptureStage(
        4,
        "profile_update",
        "Generate profile update batches and optionally auto-apply them.",
        "uv run aijournal ops profile update --date YYYY-MM-DD",
    ),
    CaptureStage(
        5,
        "index_refresh",
        "Refresh the retrieval index for new evidence.",
        "uv run aijournal ops index update --since 7d",
    ),
    CaptureStage(
        6,
        "persona_refresh",
        "Rebuild persona core when profile data changes.",
        "uv run aijournal ops persona build",
    ),
    CaptureStage(
        7,
        "pack",
        "Emit context packs when requested (depends on --pack option).",
        "uv run aijournal export pack --level Lx [--date YYYY-MM-DD]",
    ),
]

CAPTURE_MAX_STAGE = CAPTURE_STAGES[-1].stage_id


def _stage_status(result: OperationResult) -> str:
    status = result.details.get("status") if result.details else None
    if status:
        return str(status)
    if not result.ok:
        return "error"
    if result.changed:
        return "ok"
    return "noop"


def _emit_stage_event(
    log_event: Callable[[dict[str, object]], None],
    stage_result: StageResult,
    *,
    status: str | None = None,
) -> None:
    resolved_status = status or _stage_status(stage_result.result)
    payload: dict[str, object] = {
        "event": stage_result.stage,
        "status": resolved_status,
        "duration_ms": round(stage_result.duration_ms, 3),
    }
    if stage_result.result.message:
        payload["message"] = stage_result.result.message
    if stage_result.result.artifacts:
        payload["artifacts"] = stage_result.result.artifacts
    if stage_result.result.details:
        payload["details"] = stage_result.result.details
    if stage_result.result.warnings:
        payload["warnings"] = stage_result.result.warnings
    log_event(payload)


class PersistStage0Outputs(NamedTuple):
    entries: list[EntryResult]
    result: OperationResult
    duration_ms: float


class NormalizeStageOutputs(NamedTuple):
    artifacts: dict[str, Any]
    result: OperationResult
    duration_ms: float
    changed_dates: list[str]


class SummarizeStage2Outputs(NamedTuple):
    result: OperationResult
    duration_ms: float
    paths: list[str]


class FactsStage3Outputs(NamedTuple):
    result: OperationResult
    duration_ms: float
    paths: list[str]


class ProfileUpdateStageOutputs(NamedTuple):
    result: OperationResult
    review_result: OperationResult | None
    duration_ms: float
    new_batches: list[str]
    applied_batches: list[str]
    pending_batches: list[str]
    review_candidates: list[str]


class IndexStage6Outputs(NamedTuple):
    result: OperationResult
    duration_ms: float
    updated: bool
    rebuilt: bool


class PersonaStage7Outputs(NamedTuple):
    result: OperationResult
    duration_ms: float
    persona_changed: bool
    persona_stale_before: bool
    persona_stale_after: bool
    status_before: str
    status_after: str
    error: str | None


class PackStage8Outputs(NamedTuple):
    result: OperationResult
    duration_ms: float


def _record_stage(
    *,
    stage_results: list[StageResult],
    stages_completed: set[int],
    stages_skipped: set[int],
    warnings_accumulator: list[str],
    log_event: Callable[[dict[str, object]], None],
    stage_id: int,
    stage_name: str,
    op_result: OperationResult,
    duration_ms: float,
) -> None:
    stage_result = StageResult(stage=stage_name, result=op_result, duration_ms=duration_ms)
    stage_results.append(stage_result)
    if op_result.warnings:
        warnings_accumulator.extend(op_result.warnings)
    status = _stage_status(op_result)
    if status == "skipped":
        stages_skipped.add(stage_id)
    elif op_result.ok or status in {"ok", "noop"}:
        stages_completed.add(stage_id)
    _emit_stage_event(log_event, stage_result, status=status)


def _generate_run_id() -> str:
    """Return a monotonic-ish identifier for a capture run."""
    return f"capture-{time_utils.now().strftime('%Y%m%d%H%M%S')}"


def _telemetry_log_path(root: Path, run_id: str) -> Path:
    return root / "derived" / "logs" / "capture" / f"{run_id}.jsonl"


def _make_telemetry_logger(
    root: Path,
    run_id: str,
    *,
    sink: Callable[[dict[str, object]], None] | None = None,
) -> tuple[Callable[[dict[str, object]], None], Path]:
    log_path = _telemetry_log_path(root, run_id)
    sinks: list[Callable[[dict[str, object]], None]] = []
    if sink is not None:
        sinks.append(sink)
    logger = StructuredLogger(
        path=log_path,
        base={"run_id": run_id, "command": "capture"},
        sinks=sinks,
        enabled=True,
    )

    def emit_wrapper(event: dict[str, object]) -> None:
        logger.emit(**event)

    return emit_wrapper, log_path


def _capture_result_path(root: Path, run_id: str) -> Path:
    return root / "derived" / "logs" / "capture" / f"{run_id}.result.json"


def _write_capture_result(root: Path, result: CaptureResult) -> Path:
    path = _capture_result_path(root, result.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Persist JSON so other processes (FastAPI) can retrieve run metadata.
    payload = result.model_dump(mode="json")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_capture_result(root: Path, run_id: str) -> CaptureResult:
    path = _capture_result_path(root, run_id)
    if not path.exists():
        msg = f"capture run not found: {run_id}"
        raise FileNotFoundError(msg)
    data = json.loads(path.read_text(encoding="utf-8"))
    return CaptureResult.model_validate(data)


class CaptureResult(BaseModel):
    """Aggregate result for a capture run."""

    run_id: str = Field(..., description="Unique identifier for the capture run.")
    entries: list[EntryResult] = Field(default_factory=list, description="Per-entry outcomes.")
    artifacts_changed: dict[str, int] = Field(
        default_factory=dict,
        description="Counts of downstream artifacts touched by type.",
    )
    persona_stale_before: bool = Field(
        False,
        description="Whether persona was stale before capture executed.",
    )
    persona_stale_after: bool = Field(
        False,
        description="Whether persona remains stale after capture steps.",
    )
    index_rebuilt: bool = Field(False, description="True when the index was fully rebuilt.")
    warnings: list[str] = Field(default_factory=list, description="Warnings raised during capture.")
    errors: list[str] = Field(default_factory=list, description="Fatal errors encountered.")
    durations_ms: dict[str, float] = Field(
        default_factory=dict,
        description="Per-stage durations (milliseconds).",
    )
    review_candidates: list[str] = Field(
        default_factory=list,
        description="Pending review batch paths generated during capture.",
    )
    telemetry_path: str | None = Field(
        None,
        description="Relative path to the NDJSON telemetry log for this run.",
    )
    min_stage: int = Field(0, description="Requested minimum stage executed.")
    max_stage: int = Field(CAPTURE_MAX_STAGE, description="Requested maximum stage executed.")
    stages_completed: list[int] = Field(
        default_factory=list,
        description="Capture stage indices that ran successfully.",
    )
    stages_skipped: list[int] = Field(
        default_factory=list,
        description="Capture stage indices skipped by stage filters.",
    )
    stage_results: list[StageResult] = Field(
        default_factory=list,
        description="Detailed per-stage execution results.",
    )


def run_capture(
    inputs: CaptureInput,
    *,
    run_id: str | None = None,
    event_sink: Callable[[dict[str, object]], None] | None = None,
    root: Path | None = None,
) -> CaptureResult:
    """Execute the capture workflow (persist, normalize, derive, telemetry)."""
    if inputs.dry_run:
        msg = "capture dry-run is not implemented yet"
        raise ValueError(msg)

    root = root or Path.cwd()
    config = load_config_with_overrides(root, llm_retries=inputs.retries)

    ollama_config = build_ollama_config_from_mapping(config)
    config_host = config.host
    env_host = os.getenv("AIJOURNAL_OLLAMA_HOST")
    env_base_url = os.getenv("OLLAMA_BASE_URL")
    resolved_run_id = run_id or _generate_run_id()
    log_event, telemetry_path = _make_telemetry_logger(root, resolved_run_id, sink=event_sink)
    log_event(
        {
            "event": "preflight",
            "source": inputs.source,
            "paths": inputs.paths,
            "snapshot": inputs.snapshot,
            "apply_profile": inputs.apply_profile,
            "rebuild": inputs.rebuild,
            "pack": inputs.pack,
            "ollama": {
                "model": ollama_config.model,
                "host": ollama_config.host,
                "config_host": config_host,
                "env_host": env_host,
                "env_base_url": env_base_url,
            },
        },
    )

    if inputs.source not in {"stdin", "editor", "file", "dir"}:
        msg = f"Unsupported capture source: {inputs.source}"
        log_event({"event": "preflight", "status": "error", "error": msg})
        raise ValueError(msg)

    requested_min_stage = max(0, min(inputs.min_stage, CAPTURE_MAX_STAGE))
    requested_max_stage = max(0, min(inputs.max_stage, CAPTURE_MAX_STAGE))
    if requested_min_stage > requested_max_stage:
        msg = "min_stage cannot be greater than max_stage"
        log_event({"event": "preflight", "status": "error", "error": msg})
        raise ValueError(msg)

    stages_completed: set[int] = set()
    stages_skipped: set[int] = set()

    def stage_enabled(stage_index: int) -> bool:
        if stage_index <= 1:
            return stage_index <= requested_max_stage
        return requested_min_stage <= stage_index <= requested_max_stage

    manifest_entries: list[ManifestEntry] = []
    entry_results: list[EntryResult] = []
    durations_ms: dict[str, float] = {}
    warnings: list[str] = []
    review_candidates: list[str] = []
    stage_results: list[StageResult] = []

    def record_stage_outcome(
        stage_id: int,
        stage_name: str,
        duration_key: str,
        result: OperationResult,
        duration: float,
    ) -> OperationResult:
        """Track stage result, duration, and telemetry in one place."""
        durations_ms[duration_key] = duration
        _record_stage(
            stage_results=stage_results,
            stages_completed=stages_completed,
            stages_skipped=stages_skipped,
            warnings_accumulator=warnings,
            log_event=log_event,
            stage_id=stage_id,
            stage_name=stage_name,
            op_result=result,
            duration_ms=duration,
        )
        return result

    def record_skipped_stage(
        stage_id: int,
        stage_name: str,
        duration_key: str,
        *,
        message: str = "skipped by stage filter",
    ) -> OperationResult:
        """Create a standardized skip result and record it."""
        skip_result = OperationResult.noop(message, details={"status": "skipped"})
        return record_stage_outcome(
            stage_id,
            stage_name,
            duration_key,
            skip_result,
            0.0,
        )

    if stage_enabled(0):
        persist_outputs = run_persist_stage_0(
            inputs,
            root,
            config,
            manifest_entries,
            log_event,
        )
        entry_results = persist_outputs.entries
        persist_result = persist_outputs.result
        persist_duration = persist_outputs.duration_ms
        record_stage_outcome(
            stage_id=0,
            stage_name="persist",
            duration_key="persist",
            result=persist_result,
            duration=persist_duration,
        )
    else:
        entry_results = []
        persist_result = record_skipped_stage(0, "persist", "persist")

    artifact_counts: dict[str, Any] = {}
    changed_dates: list[str] = []

    if stage_enabled(1):
        normalize_outputs = run_normalize_stage_1(
            entry_results,
            root,
            config,
        )
        artifact_counts = normalize_outputs.artifacts
        normalize_result = normalize_outputs.result
        normalize_duration = normalize_outputs.duration_ms
        changed_dates = normalize_outputs.changed_dates
        record_stage_outcome(
            stage_id=1,
            stage_name="normalize",
            duration_key="normalize",
            result=normalize_result,
            duration=normalize_duration,
        )
    else:
        normalize_result = record_skipped_stage(1, "normalize", "normalize")
        normalize_duration = 0.0

    artifacts_changed = {
        key: value for key, value in artifact_counts.items() if key != "paths" and value
    }
    if stage_enabled(1):
        entries_changed = sum(1 for entry in entry_results if entry.changed and not entry.deduped)
        if entries_changed:
            artifacts_changed.setdefault("entries", entries_changed)
    else:
        entries_changed = 0

    if changed_dates and stage_enabled(2):
        summarize_outputs = run_summarize_stage_2(
            changed_dates,
            inputs,
            root,
            config,
        )
        summarize_result = summarize_outputs.result
        summarize_duration = summarize_outputs.duration_ms
        summary_paths = summarize_outputs.paths
        for _ in summary_paths:
            artifacts_changed["summaries"] = artifacts_changed.get("summaries", 0) + 1
        record_stage_outcome(
            stage_id=2,
            stage_name="derive.summarize",
            duration_key="derive.summarize",
            result=summarize_result,
            duration=summarize_duration,
        )
    elif not stage_enabled(2):
        summarize_result = record_skipped_stage(2, "derive.summarize", "derive.summarize")
    else:
        summarize_result = OperationResult.noop(
            "no dates required summarization",
            details={"dates": []},
        )
        record_stage_outcome(
            stage_id=2,
            stage_name="derive.summarize",
            duration_key="derive.summarize",
            result=summarize_result,
            duration=0.0,
        )

    if changed_dates and stage_enabled(3):
        facts_outputs = run_facts_stage_3(
            changed_dates,
            inputs,
            root,
            config,
        )
        facts_result = facts_outputs.result
        facts_duration = facts_outputs.duration_ms
        facts_paths = facts_outputs.paths
        for _ in facts_paths:
            artifacts_changed["microfacts"] = artifacts_changed.get("microfacts", 0) + 1
        record_stage_outcome(
            stage_id=3,
            stage_name="derive.extract_facts",
            duration_key="derive.extract_facts",
            result=facts_result,
            duration=facts_duration,
        )
    elif not stage_enabled(3):
        facts_result = record_skipped_stage(3, "derive.extract_facts", "derive.extract_facts")
    else:
        facts_result = OperationResult.noop(
            "no dates required micro-facts",
            details={"dates": []},
        )
        record_stage_outcome(
            stage_id=3,
            stage_name="derive.extract_facts",
            duration_key="derive.extract_facts",
            result=facts_result,
            duration=0.0,
        )

    if changed_dates and stage_enabled(4):
        update_outputs = run_profile_update_stage(
            changed_dates,
            inputs,
            root,
            config,
        )
        update_result = update_outputs.result
        review_result = update_outputs.review_result
        update_duration = update_outputs.duration_ms
        update_paths = update_outputs.new_batches
        applied_batches = update_outputs.applied_batches
        review_candidates.extend(update_outputs.review_candidates)
        for _ in update_paths:
            artifacts_changed["profile_updates"] = artifacts_changed.get("profile_updates", 0) + 1
        if review_result and review_result.changed:
            artifacts_changed["profile"] = artifacts_changed.get("profile", 0) + len(
                applied_batches,
            )
        record_stage_outcome(
            stage_id=4,
            stage_name="derive.profile_update",
            duration_key="derive.profile_update",
            result=update_result,
            duration=update_duration,
        )
        if review_result is not None:
            record_stage_outcome(
                stage_id=4,
                stage_name="derive.review",
                duration_key="derive.review",
                result=review_result,
                duration=update_duration,
            )
    elif not stage_enabled(4):
        update_result = record_skipped_stage(
            4,
            "derive.profile_update",
            "derive.profile_update",
        )
    else:
        update_result = OperationResult.noop(
            "no dates required profile updates",
            details={"dates": []},
        )
        record_stage_outcome(
            stage_id=4,
            stage_name="derive.profile_update",
            duration_key="derive.profile_update",
            result=update_result,
            duration=0.0,
        )

    if inputs.apply_profile != "auto" and "profile" not in artifacts_changed:
        artifacts_changed.setdefault("profile", 0)

    index_rebuilt = False
    persona_stale_before = False
    persona_stale_after = False
    persona_changed = False

    index_rebuilt_flag = False
    persona_error: str | None = None
    status_before = "unknown"
    status_after = "unknown"
    if stage_enabled(5):
        if inputs.rebuild == "skip":
            index_result = record_skipped_stage(
                6,
                "refresh.index",
                "refresh.index",
                message="skipped by --rebuild skip",
            )
        else:
            should_run_index = bool(changed_dates) or inputs.rebuild == "always"
            if should_run_index:
                index_outputs = run_index_stage_6(
                    changed_dates,
                    root,
                    inputs.rebuild,
                )
                index_result = index_outputs.result
                index_duration = index_outputs.duration_ms
                index_updated = index_outputs.updated
                index_rebuilt_flag = index_outputs.rebuilt
                if index_updated:
                    artifacts_changed["index"] = artifacts_changed.get("index", 0) + 1
                record_stage_outcome(
                    stage_id=5,
                    stage_name="refresh.index",
                    duration_key="refresh.index",
                    result=index_result,
                    duration=index_duration,
                )
                index_rebuilt = index_rebuilt or index_rebuilt_flag
            else:
                index_result = OperationResult.noop(
                    "no index refresh required",
                    details={"mode": inputs.rebuild, "reason": "no changed dates"},
                )
                record_stage_outcome(
                    stage_id=5,
                    stage_name="refresh.index",
                    duration_key="refresh.index",
                    result=index_result,
                    duration=0.0,
                )
    else:
        index_result = record_skipped_stage(5, "refresh.index", "refresh.index")
    emit_operation_event(
        log_event,
        event="index.rebuild",
        status=_stage_status(index_result),
        result=index_result,
    )

    if stage_enabled(6):
        if inputs.rebuild == "skip":
            persona_result = record_skipped_stage(
                7,
                "refresh.persona",
                "refresh.persona",
                message="skipped by --rebuild skip",
            )
        else:
            persona_outputs = run_persona_stage_7(inputs, root, config, artifacts_changed)
            persona_result = persona_outputs.result
            persona_duration = persona_outputs.duration_ms
            persona_changed = persona_outputs.persona_changed
            persona_stale_before = persona_outputs.persona_stale_before
            persona_stale_after = persona_outputs.persona_stale_after
            status_before = persona_outputs.status_before
            status_after = persona_outputs.status_after
            persona_error = persona_outputs.error
            if persona_changed:
                artifacts_changed["persona"] = artifacts_changed.get("persona", 0) + 1
            record_stage_outcome(
                stage_id=6,
                stage_name="refresh.persona",
                duration_key="refresh.persona",
                result=persona_result,
                duration=persona_duration,
            )
    else:
        persona_result = record_skipped_stage(6, "refresh.persona", "refresh.persona")
    persona_event_details = dict(persona_result.details or {})
    persona_event_details.update(
        {
            "status_before": status_before,
            "status_after": status_after,
        },
    )
    emit_operation_event(
        log_event,
        event="persona.status",
        status=_stage_status(persona_result),
        result=persona_result,
        details=persona_event_details,
        extra={"error": persona_error} if persona_error else None,
    )

    if stage_enabled(7):
        pack_outputs = run_pack_stage_8(
            inputs,
            root,
            resolved_run_id,
            persona_changed,
        )
        pack_result = pack_outputs.result
        pack_duration = pack_outputs.duration_ms
        if pack_result.changed:
            artifacts_changed["pack"] = artifacts_changed.get("pack", 0) + 1
        record_stage_outcome(
            stage_id=7,
            stage_name="derive.pack",
            duration_key="derive.pack",
            result=pack_result,
            duration=pack_duration,
        )
    else:
        pack_result = record_skipped_stage(7, "derive.pack", "derive.pack")

    telemetry_rel = relative_path(telemetry_path, root)
    log_event(
        {
            "event": "done",
            "status": "ok",
            "warnings": warnings,
            "artifacts_changed": artifacts_changed,
            "review_candidates": review_candidates,
            "min_stage": requested_min_stage,
            "max_stage": requested_max_stage,
            "stages_completed": sorted(stages_completed),
            "stages_skipped": sorted(stages_skipped),
        },
    )

    result = CaptureResult(
        run_id=resolved_run_id,
        entries=entry_results,
        artifacts_changed=artifacts_changed,
        persona_stale_before=persona_stale_before,
        persona_stale_after=persona_stale_after,
        index_rebuilt=index_rebuilt,
        durations_ms={key: round(value, 3) for key, value in durations_ms.items()},
        warnings=warnings,
        review_candidates=review_candidates,
        telemetry_path=telemetry_rel,
        min_stage=requested_min_stage,
        max_stage=requested_max_stage,
        stages_completed=sorted(stages_completed),
        stages_skipped=sorted(stages_skipped),
        stage_results=stage_results,
    )

    _write_capture_result(root, result)

    return result


def normalize_entries(entries: list[EntryResult], root: Path, config: AppConfig) -> dict[str, Any]:
    """Normalize Markdown entries that changed during capture."""
    normalized = 0
    changed_paths: list[str] = []
    for entry in entries:
        if not entry.markdown_path:
            continue
        if not entry.changed and entry.normalized_path:
            # Assume already normalized when unchanged.
            continue
        markdown_path = root / entry.markdown_path
        if not markdown_path.exists():
            continue
        source_hash = entry.source_hash or digest_bytes(markdown_path.read_bytes())
        source_type = entry.source_type or "journal"
        normalized_path, changed = normalize_markdown(
            markdown_path,
            root=root,
            config=config,
            source_hash=source_hash,
            source_type=source_type,
        )
        if changed:
            normalized += 1
            changed_paths.append(relative_path(normalized_path, root))
        entry.normalized_path = relative_path(normalized_path, root)
    return {"normalized": normalized, "paths": changed_paths}
