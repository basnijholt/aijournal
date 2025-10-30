"""Schemas and entry point for the capture orchestrator (Phase 2 scaffold)."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import yaml
from pydantic import BaseModel, Field

from aijournal.api.capture import CaptureInput
from aijournal.commands.ingest import _load_config
from aijournal.domain.journal import NormalizedEntry
from aijournal.models.authoritative import JournalSection, ManifestEntry
from aijournal.services.capture.results import OperationResult, StageResult
from aijournal.services.ollama import build_ollama_config_from_mapping
from aijournal.utils import time as time_utils
from aijournal.utils.paths import normalized_entry_path

from .stages.stage0_persist import run_persist_stage_0
from .stages.stage1_normalize import run_normalize_stage_1
from .stages.stage2_summarize import run_summarize_stage_2
from .stages.stage3_facts import run_facts_stage_3
from .stages.stage4_profile import run_profile_stage_4
from .stages.stage5_characterize import run_characterize_stage_5
from .stages.stage6_index import run_index_stage_6
from .stages.stage7_persona import run_persona_stage_7
from .stages.stage8_pack import run_pack_stage_8
from .utils import (
    digest_bytes,
    emit_operation_event,
    relative_path,
    write_yaml_if_changed,
)


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
        "Generate profile suggestions and optionally apply them.",
        "uv run aijournal ops profile suggest --date YYYY-MM-DD\nuv run aijournal ops profile apply --date YYYY-MM-DD --yes",
    ),
    CaptureStage(
        5,
        "characterize_review",
        "Characterize entries and review new batches (auto-applied in capture).",
        "uv run aijournal ops pipeline characterize --date YYYY-MM-DD\nuv run aijournal ops pipeline review --file <batch>.yaml --apply",
    ),
    CaptureStage(
        6,
        "index_refresh",
        "Refresh the retrieval index for new evidence.",
        "uv run aijournal ops index update --since 7d",
    ),
    CaptureStage(
        7,
        "persona_refresh",
        "Rebuild persona core when profile data changes.",
        "uv run aijournal ops persona build",
    ),
    CaptureStage(
        8,
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


class CharacterizeStage5Outputs(NamedTuple):
    result: OperationResult
    review_result: OperationResult | None
    duration_ms: float
    new_batches: list[str]
    applied_batches: list[str]
    pending_batches: list[str]
    review_candidates: list[str]


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


class ProfileStage4Outputs(NamedTuple):
    suggest_result: OperationResult
    apply_result: OperationResult | None
    duration_ms: float
    suggestion_paths: list[str]
    applied_count: int


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
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(event: dict[str, object]) -> None:
        payload = {
            "run_id": run_id,
            "timestamp": time_utils.format_timestamp(time_utils.now()),
            **event,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        if sink is not None:
            try:
                sink(payload)
            except Exception:  # pragma: no cover - defensive sink guard
                return

    return _write, log_path


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


DEFAULT_TIMEOUT_SECONDS = 120.0


def _resolve_created_dt(preferred: object, fallback: datetime) -> datetime:
    if preferred:
        if isinstance(preferred, datetime):
            parsed = preferred
        elif (
            hasattr(preferred, "year") and hasattr(preferred, "month") and hasattr(preferred, "day")
        ):
            parsed = datetime(preferred.year, preferred.month, preferred.day, tzinfo=UTC)
        else:
            text = str(preferred)
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                parsed = datetime.strptime(text, "%Y-%m-%d")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    return fallback


def _coerce_frontmatter_tags(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw if isinstance(item, (str, int, float))]
    if isinstance(raw, str):
        return [raw]
    return []


def _extract_json_frontmatter_block(text: str) -> tuple[str, str]:
    depth = 0
    in_string = False
    escape = False
    start_index = None
    for index, char in enumerate(text):
        if start_index is None:
            if char.isspace():
                continue
            if char != "{":
                raise ValueError("JSON frontmatter must start with '{'")
            start_index = index
            depth = 1
            continue

        if in_string:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0 and start_index is not None:
                end_index = index + 1
                block = text[start_index:end_index]
                remainder = text[end_index:]
                return block, remainder
    raise ValueError("Unterminated JSON frontmatter block")


def _extract_json_frontmatter(text: str) -> tuple[dict[str, object], str]:
    block, body = _extract_json_frontmatter_block(text)
    try:
        data = json.loads(block) or {}
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise ValueError("Invalid JSON frontmatter") from exc
    if not isinstance(data, dict):
        data = {}
    return data, body.lstrip("\n")


def _normalize_markdown(
    markdown_path: Path,
    *,
    root: Path,
    source_hash: str,
    source_type: str,
) -> tuple[Path, bool]:
    frontmatter, body = _split_frontmatter(markdown_path.read_text(encoding="utf-8"))

    created_dt = _resolve_created_dt(frontmatter.get("created_at"), time_utils.now())
    created_str = time_utils.format_timestamp(created_dt)
    date_str = created_dt.strftime("%Y-%m-%d")

    entry_id_raw = frontmatter.get("id") or frontmatter.get("slug")
    if entry_id_raw is None:
        entry_id_raw = markdown_path.stem
    entry_id = str(entry_id_raw)

    title_raw = frontmatter.get("title") or entry_id.replace("-", " ").title()
    title = str(title_raw)

    tags = _coerce_frontmatter_tags(frontmatter.get("tags"))
    sections_raw = _scan_headings(body)
    sections_models: list[JournalSection] = []
    for section in sections_raw:
        heading = str(section.get("heading", title))
        level_raw = section.get("level", 1)
        if isinstance(level_raw, (int, float, str)):
            try:
                level = int(level_raw)
            except (TypeError, ValueError):
                level = 1
        else:
            level = 1
        sections_models.append(
            JournalSection(
                heading=heading,
                level=level,
                summary=None,
            ),
        )
    summary_raw = frontmatter.get("summary")
    summary_text = str(summary_raw) if summary_raw is not None else (body.strip() or None)
    if not sections_models:
        sections_models = [JournalSection(heading=title, level=1, summary=summary_text)]

    normalized_entry = NormalizedEntry(
        id=entry_id,
        created_at=created_str,
        source_path=relative_path(markdown_path, root),
        title=title,
        tags=tags,
        sections=sections_models,
        summary=summary_text,
        source_hash=source_hash,
        source_type=source_type,
    )
    normalized_path = normalized_entry_path(root, date_str, entry_id)
    changed = write_yaml_if_changed(
        normalized_path,
        normalized_entry.model_dump(mode="python"),
    )
    return normalized_path, changed


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return _extract_json_frontmatter(stripped)

    delimiter = None
    if stripped.startswith("---"):
        delimiter = "---"
    elif stripped.startswith("+++"):
        delimiter = "+++"
    if delimiter is None:
        msg = "Markdown entry missing YAML/TOML frontmatter delimiter"
        raise ValueError(msg)

    parts = stripped.split(delimiter, 2)
    if len(parts) < 3:
        msg = "Incomplete YAML/TOML frontmatter block"
        raise ValueError(msg)

    frontmatter_raw = parts[1].strip()
    body = parts[2].lstrip("\n")
    data = yaml.safe_load(frontmatter_raw) or {}
    if not isinstance(data, dict):
        data = {}
    return data, body


def _scan_headings(text: str) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        hashes, _, heading = stripped.partition(" ")
        if not heading:
            continue
        level = len(hashes)
        sections.append({"heading": heading.strip(), "level": level})
    return sections


class EntryResult(BaseModel):
    """Outcome for a single journal entry processed during capture."""

    markdown_path: str | None = Field(None, description="Authoritative Markdown path.")
    normalized_path: str | None = Field(None, description="Normalized YAML emitted for the entry.")
    date: str = Field(..., description="Date bucket for the entry (YYYY-MM-DD).")
    slug: str = Field(..., description="Slug assigned to the entry.")
    deduped: bool = Field(
        False, description="True when the input was skipped due to identical hash."
    )
    changed: bool = Field(False, description="True when content or metadata changed on disk.")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal issues encountered.")
    source_hash: str | None = Field(
        None, description="Hash of the Markdown content used for dedupe/normalization."
    )
    source_type: str | None = Field(
        None, description="Source type recorded for the entry (journal/notes/blog)."
    )


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
    config_payload = _load_config(root)
    ollama_config = build_ollama_config_from_mapping(config_payload)
    config_host = config_payload.get("host") if isinstance(config_payload, dict) else None
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
        }
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
    else:
        if not stage_enabled(2):
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
    else:
        if not stage_enabled(3):
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
        profile_outputs = run_profile_stage_4(
            changed_dates,
            inputs,
            root,
        )
        profile_result = profile_outputs.suggest_result
        apply_result = profile_outputs.apply_result
        profile_duration = profile_outputs.duration_ms
        suggestion_paths = profile_outputs.suggestion_paths
        applied_count = profile_outputs.applied_count
        for _ in suggestion_paths:
            artifacts_changed["profile_suggestions"] = (
                artifacts_changed.get("profile_suggestions", 0) + 1
            )
        if apply_result and apply_result.changed:
            artifacts_changed["profile"] = artifacts_changed.get("profile", 0) + applied_count
        record_stage_outcome(
            stage_id=4,
            stage_name="derive.profile_suggest",
            duration_key="derive.profile_suggest",
            result=profile_result,
            duration=profile_duration,
        )
        if apply_result is not None:
            record_stage_outcome(
                stage_id=4,
                stage_name="derive.profile_apply",
                duration_key="derive.profile_apply",
                result=apply_result,
                duration=profile_duration,
            )
    else:
        if not stage_enabled(4):
            profile_result = record_skipped_stage(
                4,
                "derive.profile_suggest",
                "derive.profile_suggest",
            )
        else:
            profile_result = OperationResult.noop(
                "no dates required profile suggestions",
                details={"dates": []},
            )
            record_stage_outcome(
                stage_id=4,
                stage_name="derive.profile_suggest",
                duration_key="derive.profile_suggest",
                result=profile_result,
                duration=0.0,
            )

    if changed_dates and stage_enabled(5):
        characterize_outputs = run_characterize_stage_5(changed_dates, inputs, root)
        characterize_result = characterize_outputs.result
        review_result = characterize_outputs.review_result
        characterize_duration = characterize_outputs.duration_ms
        characterize_paths = characterize_outputs.new_batches
        review_applied = characterize_outputs.applied_batches
        review_candidates_generated = characterize_outputs.review_candidates
        for path in characterize_paths:
            artifacts_changed["characterize"] = artifacts_changed.get("characterize", 0) + 1
        review_candidates.extend(review_candidates_generated)
        if review_result and review_result.changed:
            artifacts_changed["profile"] = artifacts_changed.get("profile", 0) + len(review_applied)
        record_stage_outcome(
            stage_id=5,
            stage_name="derive.characterize",
            duration_key="derive.characterize",
            result=characterize_result,
            duration=characterize_duration,
        )
        if review_result is not None:
            record_stage_outcome(
                stage_id=5,
                stage_name="derive.review",
                duration_key="derive.review",
                result=review_result,
                duration=characterize_duration,
            )
    else:
        if not stage_enabled(5):
            characterize_result = record_skipped_stage(
                5,
                "derive.characterize",
                "derive.characterize",
            )
        else:
            characterize_result = OperationResult.noop(
                "no dates required characterization",
                details={"dates": []},
            )
            record_stage_outcome(
                stage_id=5,
                stage_name="derive.characterize",
                duration_key="derive.characterize",
                result=characterize_result,
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
    if stage_enabled(6):
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
                    stage_id=6,
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
                    stage_id=6,
                    stage_name="refresh.index",
                    duration_key="refresh.index",
                    result=index_result,
                    duration=0.0,
                )
    else:
        index_result = record_skipped_stage(6, "refresh.index", "refresh.index")
    emit_operation_event(
        log_event,
        event="index.rebuild",
        status=_stage_status(index_result),
        result=index_result,
    )

    if stage_enabled(7):
        if inputs.rebuild == "skip":
            persona_result = record_skipped_stage(
                7,
                "refresh.persona",
                "refresh.persona",
                message="skipped by --rebuild skip",
            )
        else:
            persona_outputs = run_persona_stage_7(inputs, root, artifacts_changed)
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
                stage_id=7,
                stage_name="refresh.persona",
                duration_key="refresh.persona",
                result=persona_result,
                duration=persona_duration,
            )
    else:
        persona_result = record_skipped_stage(7, "refresh.persona", "refresh.persona")
    persona_event_details = dict(persona_result.details or {})
    persona_event_details.update(
        {
            "status_before": status_before,
            "status_after": status_after,
        }
    )
    emit_operation_event(
        log_event,
        event="persona.status",
        status=_stage_status(persona_result),
        result=persona_result,
        details=persona_event_details,
        extra={"error": persona_error} if persona_error else None,
    )

    if stage_enabled(8):
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
            stage_id=8,
            stage_name="pack",
            duration_key="refresh.pack",
            result=pack_result,
            duration=pack_duration,
        )
    else:
        pack_result = record_skipped_stage(8, "pack", "refresh.pack")

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
        }
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


def normalize_entries(entries: list[EntryResult], root: Path) -> dict[str, Any]:
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
        normalized_path, changed = _normalize_markdown(
            markdown_path,
            root=root,
            source_hash=source_hash,
            source_type=source_type,
        )
        if changed:
            normalized += 1
            changed_paths.append(relative_path(normalized_path, root))
        entry.normalized_path = relative_path(normalized_path, root)
    return {"normalized": normalized, "paths": changed_paths}
