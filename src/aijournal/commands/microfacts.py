"""Operations for global microfact consolidation artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.facts import (
    ConsolidatedMicroFact,
    ConsolidatedMicrofactsFile,
    MicrofactConsolidationLog,
    MicrofactConsolidationSummary,
)
from aijournal.io.artifacts import save_artifact
from aijournal.services.microfacts.index import (
    MicrofactConsolidationStats,
    MicrofactIndex,
    MicrofactRecord,
)
from aijournal.utils import time as time_utils

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aijournal.common.app_config import AppConfig
    from aijournal.common.context import RunContext


class MicrofactsRebuildOptions(BaseModel):
    """User-provided parameters for consolidating microfacts."""

    model_config = ConfigDict(arbitrary_types_allowed=True)


@dataclass(slots=True)
class MicrofactsRebuildPrepared:
    workspace: Path
    config: AppConfig


@dataclass(slots=True)
class MicrofactsRebuildResult:
    consolidated: ConsolidatedMicrofactsFile
    log: MicrofactConsolidationLog
    consolidated_path: Path
    log_path: Path


def _derived_dir(workspace: Path, config: AppConfig) -> Path:
    derived = Path(config.paths.derived)
    if not derived.is_absolute():
        derived = workspace / derived
    return derived


def _consolidated_path(workspace: Path, config: AppConfig) -> Path:
    return _derived_dir(workspace, config) / "microfacts" / "consolidated.yaml"


def _log_path(workspace: Path, config: AppConfig, *, timestamp: str) -> Path:
    return _derived_dir(workspace, config) / "microfacts" / "logs" / f"rebuild-{timestamp}.yaml"


def prepare_inputs(ctx: RunContext, options: MicrofactsRebuildOptions) -> MicrofactsRebuildPrepared:
    del options  # unused for now; placeholder for future filters
    return MicrofactsRebuildPrepared(workspace=ctx.workspace, config=ctx.config)


def _record_to_consolidated(record: MicrofactRecord) -> ConsolidatedMicroFact:
    return ConsolidatedMicroFact(
        id=record.uid,
        statement=record.statement,
        canonical_statement=record.canonical_statement,
        confidence=record.confidence,
        first_seen=record.first_seen,
        last_seen=record.last_seen,
        observation_count=record.observation_count,
        domain=record.domain,
        contexts=list(record.contexts),
        evidence_entries=list(record.evidence_entries),
        source_fact_ids=list(record.source_fact_ids),
    )


def _build_consolidated_artifact(
    *,
    records: Sequence[MicrofactRecord],
    generated_at: str,
    embedding_model: str | None,
) -> ConsolidatedMicrofactsFile:
    sorted_records = sorted(
        records,
        key=lambda rec: (rec.canonical_statement, rec.first_seen, rec.uid),
    )
    return ConsolidatedMicrofactsFile(
        generated_at=generated_at,
        embedding_model=embedding_model,
        facts=[_record_to_consolidated(rec) for rec in sorted_records],
    )


def _build_log(
    *,
    stats: Sequence[MicrofactConsolidationStats],
    generated_at: str,
) -> MicrofactConsolidationLog:
    entries = [
        MicrofactConsolidationSummary(
            day=stat.day,
            processed=stat.processed,
            new_records=stat.new_records,
            merged_records=stat.merged_records,
        )
        for stat in stats
    ]
    return MicrofactConsolidationLog(generated_at=generated_at, entries=entries)


def invoke_pipeline(
    ctx: RunContext,
    prepared: MicrofactsRebuildPrepared,
) -> MicrofactsRebuildResult:
    index = MicrofactIndex(prepared.workspace, prepared.config, fake_mode=ctx.use_fake_llm)
    rebuild = index.rebuild_from_daily_artifacts()
    timestamp = time_utils.format_timestamp(time_utils.now())
    consolidated = _build_consolidated_artifact(
        records=rebuild.facts,
        generated_at=timestamp,
        embedding_model=index.embedder.model,
    )
    log = _build_log(stats=rebuild.stats, generated_at=timestamp)
    return MicrofactsRebuildResult(
        consolidated=consolidated,
        log=log,
        consolidated_path=_consolidated_path(prepared.workspace, prepared.config),
        log_path=_log_path(
            prepared.workspace,
            prepared.config,
            timestamp=timestamp.replace(":", "-").replace("T", "_").replace("Z", ""),
        ),
    )


def persist_output(ctx: RunContext, result: MicrofactsRebuildResult) -> MicrofactsRebuildResult:
    save_artifact(
        result.consolidated_path,
        Artifact[ConsolidatedMicrofactsFile](
            kind=ArtifactKind.MICROFACTS_CONSOLIDATED,
            meta=ArtifactMeta(
                created_at=result.consolidated.generated_at,
                model=ctx.config.embedding_model,
                prompt_path="microfacts.consolidated",
                prompt_hash=None,
            ),
            data=result.consolidated,
        ),
    )
    save_artifact(
        result.log_path,
        Artifact[MicrofactConsolidationLog](
            kind=ArtifactKind.MICROFACTS_LOG,
            meta=ArtifactMeta(
                created_at=result.log.generated_at,
                model=None,
                prompt_path="microfacts.rebuild",
                prompt_hash=None,
            ),
            data=result.log,
        ),
    )
    return result


def run_microfacts_rebuild(
    ctx: RunContext,
    options: MicrofactsRebuildOptions,
) -> MicrofactsRebuildResult:
    return run_command_pipeline(
        ctx,
        options,
        prepare_inputs=prepare_inputs,
        invoke_pipeline=invoke_pipeline,
        persist_output=persist_output,
    )
