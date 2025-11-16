"""Stage-level validators used by the human simulator harness."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import yaml

from aijournal.domain.facts import DailySummary, MicroFactsFile
from aijournal.domain.journal import NormalizedEntry
from aijournal.domain.packs import PackBundle
from aijournal.domain.persona import PersonaCore
from aijournal.io.artifacts import load_artifact_data
from aijournal.io.yaml_io import load_yaml_model
from aijournal.models.authoritative import ManifestEntry
from aijournal.models.derived import ProfileUpdateBatch

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from aijournal.services.capture import CaptureResult

STAGE_NAMES = {
    0: "persist",
    1: "normalize",
    2: "derive.summarize",
    3: "derive.extract_facts",
    4: "derive.profile_update",
    5: "refresh.index",
    6: "refresh.persona",
    7: "derive.pack",
}


@dataclass(slots=True)
class ValidationFailure:
    stage_id: int
    invariant: str
    message: str
    severity: str = "error"
    date: str | None = None
    file: str | None = None

    @property
    def stage(self) -> str:
        return STAGE_NAMES.get(self.stage_id, f"stage-{self.stage_id}")


@dataclass(slots=True)
class ValidationReport:
    failures: list[ValidationFailure]

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.failures)

    def errors(self) -> list[ValidationFailure]:
        return [failure for failure in self.failures if failure.severity == "error"]


@dataclass(slots=True)
class ValidatorContext:
    workspace: Path
    capture: CaptureResult


class StageValidator(Protocol):
    stage_id: int

    def validate(self, ctx: ValidatorContext) -> list[ValidationFailure]: ...


class Stage0Validator:
    stage_id = 0

    def validate(self, ctx: ValidatorContext) -> list[ValidationFailure]:
        failures: list[ValidationFailure] = []
        manifest_entries = _load_manifest(ctx.workspace)
        manifest_by_id = {entry.id: entry for entry in manifest_entries if entry.id}

        manifest_path = ctx.workspace / "data" / "manifest" / "ingested.yaml"
        if not manifest_path.exists():
            failures.append(
                ValidationFailure(
                    stage_id=self.stage_id,
                    invariant="manifest-present",
                    message="Manifest file data/manifest/ingested.yaml is missing",
                ),
            )

        for entry in ctx.capture.entries:
            if entry.deduped:
                continue

            markdown_rel = entry.markdown_path
            markdown_abs = ctx.workspace / markdown_rel if markdown_rel else None
            if markdown_abs is None or not markdown_abs.exists():
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="markdown-written",
                        message="Canonical Markdown file missing",
                        date=entry.date,
                        file=markdown_rel,
                    ),
                )

            manifest_entry = manifest_by_id.get(entry.slug)
            if manifest_entry is None:
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="manifest-row",
                        message="Entry missing from manifest",
                        date=entry.date,
                        file=entry.markdown_path,
                    ),
                )
            else:
                created_at = manifest_entry.created_at or ""
                if not str(created_at).endswith("Z"):
                    failures.append(
                        ValidationFailure(
                            stage_id=self.stage_id,
                            invariant="created-at-utc",
                            message="Manifest created_at is not UTC (missing 'Z' suffix)",
                            date=entry.date,
                            file=entry.markdown_path,
                            severity="warning",
                        ),
                    )

            for warning in entry.warnings:
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="persist-warning",
                        message=warning,
                        date=entry.date,
                        file=entry.markdown_path,
                        severity="warning",
                    ),
                )

        return failures


class Stage1Validator:
    stage_id = 1

    def validate(self, ctx: ValidatorContext) -> list[ValidationFailure]:
        failures: list[ValidationFailure] = []

        for entry in ctx.capture.entries:
            if entry.deduped:
                continue

            normalized_rel = entry.normalized_path
            normalized_abs = ctx.workspace / normalized_rel if normalized_rel else None
            if normalized_abs is None or not normalized_abs.exists():
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="normalized-exists",
                        message="Normalized YAML missing",
                        date=entry.date,
                        file=normalized_rel,
                    ),
                )
                continue

            try:
                normalized = load_yaml_model(normalized_abs, NormalizedEntry)
            except Exception as exc:  # noqa: BLE001 - include validation errors
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="normalized-valid",
                        message=f"Failed to load NormalizedEntry: {exc}",
                        date=entry.date,
                        file=normalized_rel,
                    ),
                )
                continue

            if entry.markdown_path and normalized.source_path != entry.markdown_path:
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="source-path-alignment",
                        message="Normalized source_path does not match canonical markdown",
                        date=entry.date,
                        file=normalized_rel,
                        severity="warning",
                    ),
                )

        return failures


class Stage2Validator:
    stage_id = 2

    def validate(self, ctx: ValidatorContext) -> list[ValidationFailure]:
        failures: list[ValidationFailure] = []
        expected_dates = {
            entry.date for entry in ctx.capture.entries if entry.changed and not entry.deduped
        }
        if not expected_dates:
            return failures

        derived_root = ctx.workspace / "derived" / "summaries"
        for date in sorted(expected_dates):
            summary_path = derived_root / f"{date}.yaml"
            if not summary_path.exists():
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="summary-written",
                        message="Daily summary missing",
                        date=date,
                        file=str(summary_path.relative_to(ctx.workspace)),
                    ),
                )
                continue

            try:
                summary = load_artifact_data(summary_path, DailySummary)
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="summary-valid",
                        message=f"Failed to load summary artifact: {exc}",
                        date=date,
                        file=str(summary_path.relative_to(ctx.workspace)),
                    ),
                )
                continue

            if not summary.bullets:
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="summary-bullets",
                        message="Summary contains no bullets",
                        date=date,
                        file=str(summary_path.relative_to(ctx.workspace)),
                        severity="warning",
                    ),
                )

        return failures


class Stage3Validator:
    stage_id = 3

    def validate(self, ctx: ValidatorContext) -> list[ValidationFailure]:
        failures: list[ValidationFailure] = []
        expected_dates = _changed_dates(ctx)
        if not expected_dates:
            return failures

        manifest_entries = _load_manifest(ctx.workspace)
        valid_ids = {entry.id for entry in manifest_entries if entry.id}
        normalized_ids = _normalized_ids(ctx.workspace)

        derived_root = ctx.workspace / "derived" / "microfacts"
        for date in sorted(expected_dates):
            facts_path = derived_root / f"{date}.yaml"
            if not facts_path.exists():
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="facts-written",
                        message="Micro-facts file missing",
                        date=date,
                        file=str(facts_path.relative_to(ctx.workspace)),
                    ),
                )
                continue

            try:
                facts_file = load_artifact_data(facts_path, MicroFactsFile)
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="facts-valid",
                        message=f"Failed to load micro-facts artifact: {exc}",
                        date=date,
                        file=str(facts_path.relative_to(ctx.workspace)),
                    ),
                )
                continue

            for fact in facts_file.facts:
                if not fact.statement or len(fact.statement) > 500:
                    failures.append(
                        ValidationFailure(
                            stage_id=self.stage_id,
                            invariant="fact-statement-concise",
                            message=f"Fact statement empty or too long ({len(fact.statement)} chars): {fact.id}",
                            date=date,
                            file=str(facts_path.relative_to(ctx.workspace)),
                            severity="warning",
                        ),
                    )

                if fact.evidence.entry_id and fact.evidence.entry_id not in valid_ids:
                    failures.append(
                        ValidationFailure(
                            stage_id=self.stage_id,
                            invariant="fact-entry-id-valid",
                            message=f"Fact references non-existent entry ID: {fact.evidence.entry_id}",
                            date=date,
                            file=str(facts_path.relative_to(ctx.workspace)),
                        ),
                    )

            for proposal in facts_file.claim_proposals:
                missing_ids = [nid for nid in proposal.normalized_ids if nid not in normalized_ids]
                if missing_ids:
                    failures.append(
                        ValidationFailure(
                            stage_id=self.stage_id,
                            invariant="proposal-normalized-id-valid",
                            message=(
                                "Claim proposal references unknown normalized IDs: "
                                + ", ".join(sorted(missing_ids))
                            ),
                            date=date,
                            file=str(facts_path.relative_to(ctx.workspace)),
                        ),
                    )

        return failures


class Stage4Validator:
    stage_id = 4

    def validate(self, ctx: ValidatorContext) -> list[ValidationFailure]:
        failures: list[ValidationFailure] = []
        expected_dates = _changed_dates(ctx)
        if not expected_dates:
            return failures

        pending_root = ctx.workspace / "derived" / "pending" / "profile_updates"
        if not pending_root.exists():
            return failures

        batch_files = list(pending_root.glob("*.yaml"))
        applied_root = pending_root / "applied_feedback"
        if applied_root.exists():
            batch_files.extend(applied_root.glob("*.yaml"))

        for batch_path in batch_files:
            try:
                batch = load_artifact_data(batch_path, ProfileUpdateBatch)
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="batch-valid",
                        message=f"Failed to load profile update batch: {exc}",
                        file=str(batch_path.relative_to(ctx.workspace)),
                    ),
                )
                continue

            if batch.date not in expected_dates:
                continue

            if not batch.batch_id:
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="batch-has-id",
                        message="Profile update batch missing batch_id",
                        date=batch.date,
                        file=str(batch_path.relative_to(ctx.workspace)),
                    ),
                )

        profile_stage = _stage_result_by_name(ctx, "derive.profile_update")
        if profile_stage:
            recorded = profile_stage.result.details.get("new_batches", [])
            if len(recorded) != len(set(recorded)):
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="batch-unique",
                        message="Profile update stage reported duplicate batches",
                    ),
                )
            for rel_path in recorded:
                batch_path = ctx.workspace / rel_path
                if not batch_path.exists():
                    failures.append(
                        ValidationFailure(
                            stage_id=self.stage_id,
                            invariant="batch-files-exist",
                            message="Profile update result references a missing batch file",
                            file=rel_path,
                        ),
                    )

        review_stage = _stage_result_by_name(ctx, "derive.review")
        if review_stage:
            apply_mode = review_stage.result.details.get("apply_mode")
            applied_batches = review_stage.result.details.get("applied_batches", [])
            pending_batches = review_stage.result.details.get("pending_batches", [])
            overlap = set(applied_batches) & set(pending_batches)
            if overlap:
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="batch-review-overlap",
                        message="Batches marked as both applied and pending",
                        file=", ".join(sorted(overlap)),
                    ),
                )
            tracked_batches = set(applied_batches) | set(pending_batches)
            for rel_path in tracked_batches:
                batch_path = ctx.workspace / rel_path
                if not batch_path.exists():
                    failures.append(
                        ValidationFailure(
                            stage_id=self.stage_id,
                            invariant="batch-review-file",
                            message="Review stage references a missing batch file",
                            file=rel_path,
                        ),
                    )
            if apply_mode == "auto" and not applied_batches and recorded:
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="batch-auto-apply",
                        message="Auto review mode did not apply any profile update batches",
                        severity="warning",
                    ),
                )

        return failures


class Stage5Validator:
    stage_id = 5

    def validate(self, ctx: ValidatorContext) -> list[ValidationFailure]:
        failures: list[ValidationFailure] = []
        expected_dates = _changed_dates(ctx)
        index_root = ctx.workspace / "derived" / "index"
        chroma_dir = index_root / "chroma"
        meta_file = index_root / "meta.json"

        if not chroma_dir.exists():
            failures.append(
                ValidationFailure(
                    stage_id=self.stage_id,
                    invariant="chroma-index-exists",
                    message="Chroma index missing",
                    file=str(chroma_dir.relative_to(ctx.workspace)),
                    severity="warning",
                ),
            )

        if not meta_file.exists():
            failures.append(
                ValidationFailure(
                    stage_id=self.stage_id,
                    invariant="index-meta-exists",
                    message="Index metadata missing",
                    file=str(meta_file.relative_to(ctx.workspace)),
                    severity="warning",
                ),
            )
        else:
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="index-meta-valid",
                        message=f"Failed to parse index meta: {exc}",
                        file=str(meta_file.relative_to(ctx.workspace)),
                    ),
                )
            else:
                touched_dates = set(meta.get("touched_dates", []))
                missing = sorted(expected_dates - touched_dates)
                if missing:
                    failures.append(
                        ValidationFailure(
                            stage_id=self.stage_id,
                            invariant="index-covers-dates",
                            message=("Index metadata missing touched dates: " + ", ".join(missing)),
                        ),
                    )
                if not meta.get("updated_at"):
                    failures.append(
                        ValidationFailure(
                            stage_id=self.stage_id,
                            invariant="index-updated-at",
                            message="Index metadata missing updated_at timestamp",
                            file=str(meta_file.relative_to(ctx.workspace)),
                            severity="warning",
                        ),
                    )

        return failures


class Stage6Validator:
    stage_id = 6

    def validate(self, ctx: ValidatorContext) -> list[ValidationFailure]:
        failures: list[ValidationFailure] = []

        persona_path = ctx.workspace / "derived" / "persona" / "persona_core.yaml"
        if not persona_path.exists():
            failures.append(
                ValidationFailure(
                    stage_id=self.stage_id,
                    invariant="persona-exists",
                    message="Persona core file missing",
                    file=str(persona_path.relative_to(ctx.workspace)),
                    severity="warning",
                ),
            )
            return failures

        try:
            persona = load_artifact_data(persona_path, PersonaCore)
        except Exception as exc:  # noqa: BLE001
            failures.append(
                ValidationFailure(
                    stage_id=self.stage_id,
                    invariant="persona-valid",
                    message=f"Failed to load persona core: {exc}",
                    file=str(persona_path.relative_to(ctx.workspace)),
                ),
            )
            return failures

        if not persona.profile and not persona.claims:
            failures.append(
                ValidationFailure(
                    stage_id=self.stage_id,
                    invariant="persona-has-content",
                    message="Persona has neither profile nor claims",
                    file=str(persona_path.relative_to(ctx.workspace)),
                    severity="warning",
                ),
            )

        profile_changes = ctx.capture.artifacts_changed.get("profile", 0)
        persona_stage = _stage_result_by_name(ctx, "refresh.persona")
        if profile_changes and (persona_stage is None or not persona_stage.result.changed):
            failures.append(
                ValidationFailure(
                    stage_id=self.stage_id,
                    invariant="persona-rebuilt",
                    message="Profile changed but persona refresh did not report a rebuild",
                ),
            )

        return failures


class Stage7Validator:
    stage_id = 7

    def validate(self, ctx: ValidatorContext) -> list[ValidationFailure]:
        failures: list[ValidationFailure] = []

        pack_stage = _stage_result_by_name(ctx, "pack")
        if pack_stage is None:
            return failures

        packs_root = ctx.workspace / "derived" / "packs"
        if not packs_root.exists():
            packs_root.mkdir(parents=True, exist_ok=True)

        artifacts = pack_stage.result.artifacts
        if pack_stage.result.changed and not artifacts:
            failures.append(
                ValidationFailure(
                    stage_id=self.stage_id,
                    invariant="pack-artifact-recorded",
                    message="Pack stage reported success but no artifacts were recorded",
                ),
            )

        for rel_path in artifacts:
            pack_path = ctx.workspace / rel_path
            if not pack_path.exists():
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="pack-file-exists",
                        message="Pack artifact missing",
                        file=rel_path,
                    ),
                )
                continue
            try:
                bundle = load_artifact_data(pack_path, PackBundle)
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="pack-valid",
                        message=f"Failed to load pack: {exc}",
                        file=rel_path,
                    ),
                )
                continue
            level_detail = str(pack_stage.result.details.get("level", "")).upper()
            if level_detail and bundle.level.upper() != level_detail:
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="pack-level-match",
                        message="Pack level does not match stage details",
                        file=rel_path,
                        severity="warning",
                    ),
                )
            if bundle.meta.total_tokens > bundle.meta.max_tokens:
                failures.append(
                    ValidationFailure(
                        stage_id=self.stage_id,
                        invariant="pack-token-budget",
                        message="Pack exceeded its configured token budget",
                        file=rel_path,
                        severity="warning",
                    ),
                )

        if not pack_stage.result.changed and pack_stage.result.message.startswith(
            "persona unchanged",
        ):
            # No pack expected; treat as informational only
            return failures

        return failures


class StageValidatorRegistry:
    def __init__(self, validators: Iterable[StageValidator] | None = None) -> None:
        default_validators = (
            Stage0Validator(),
            Stage1Validator(),
            Stage2Validator(),
            Stage3Validator(),
            Stage4Validator(),
            Stage5Validator(),
            Stage6Validator(),
            Stage7Validator(),
        )
        self._validators = {
            validator.stage_id: validator for validator in (validators or default_validators)
        }

    def run(
        self,
        ctx: ValidatorContext,
        *,
        stages: Sequence[int] | None = None,
    ) -> ValidationReport:
        enabled = set(stages) if stages is not None else set(self._validators.keys())
        failures: list[ValidationFailure] = []
        for stage_id in sorted(enabled):
            validator = self._validators.get(stage_id)
            if validator is None:
                continue
            failures.extend(validator.validate(ctx))
        return ValidationReport(failures=failures)


def render_failures_compact(failures: Sequence[ValidationFailure]) -> str:
    """Return a table-style summary suitable for CLI output."""
    lines = ["stage | invariant | date | file | message"]
    for failure in failures:
        lines.append(
            " | ".join(
                [
                    failure.stage,
                    failure.invariant,
                    failure.date or "-",
                    failure.file or "-",
                    failure.message,
                ],
            ),
        )
    return "\n".join(lines)


def _load_manifest(root: Path) -> list[ManifestEntry]:
    manifest_path = root / "data" / "manifest" / "ingested.yaml"
    if not manifest_path.exists():
        return []
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or []
    entries: list[ManifestEntry] = []
    for item in raw:
        try:
            entries.append(ManifestEntry.model_validate(item))
        except Exception:  # noqa: BLE001
            continue
    return entries


def _changed_dates(ctx: ValidatorContext) -> set[str]:
    return {entry.date for entry in ctx.capture.entries if entry.changed and not entry.deduped}


def _normalized_ids(root: Path) -> set[str]:
    ids: set[str] = set()
    normalized_root = root / "data" / "normalized"
    if not normalized_root.exists():
        return ids
    for yaml_file in normalized_root.rglob("*.yaml"):
        try:
            entry = load_yaml_model(yaml_file, NormalizedEntry)
        except Exception:  # noqa: BLE001
            continue
        if entry.id:
            ids.add(entry.id)
    return ids


def _stage_result_by_name(ctx: ValidatorContext, stage_name: str):
    for stage_result in ctx.capture.stage_results:
        if stage_result.stage == stage_name:
            return stage_result
    return None
