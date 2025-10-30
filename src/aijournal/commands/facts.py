"""Orchestration helpers for the `aijournal facts` command."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import cast

import typer

from aijournal.commands.ingest import (
    _load_config,
    _load_manifest,
    _manifest_path,
    _use_fake_llm,
)
from aijournal.commands.summarize import (
    _build_meta,
    _entries_to_payload,
    _invoke_structured_llm,
    _json_block,
    _load_normalized_entries,
    _log_entry_progress,
    _structured_call_with_retry,
    _validate_timeout,
)
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.changes import ClaimProposal
from aijournal.domain.claims import ClaimAtom, ClaimSource
from aijournal.domain.facts import MicroFactsFile, SummaryMeta
from aijournal.domain.journal import NormalizedEntry
from aijournal.io.artifacts import save_artifact
from aijournal.models.authoritative import ManifestEntry
from aijournal.models.derived import ProfileUpdatePreview
from aijournal.pipelines import facts as facts_pipeline
from aijournal.services.ollama import LLMResponseError
from aijournal.utils import time as time_utils


def _manifest_by_id(entries: Iterable[ManifestEntry]) -> dict[str, ManifestEntry]:
    index: dict[str, ManifestEntry] = {}
    for entry in entries:
        entry_id = entry.id
        if not entry_id:
            continue
        index[entry_id] = entry
    return index


def _characterization_context(
    entries: Sequence[NormalizedEntry],
    manifest_index: dict[str, ManifestEntry],
) -> tuple[list[str], list[str], list[ClaimSource]]:
    normalized_ids: list[str] = []
    manifest_hashes: set[str] = set()
    default_sources: list[ClaimSource] = []

    for idx, entry in enumerate(entries):
        entry_id = entry.id or f"entry-{idx + 1}"
        normalized_ids.append(entry_id)
        manifest_entry = manifest_index.get(entry_id)
        manifest_hash = manifest_entry.hash if manifest_entry else None
        if manifest_hash:
            manifest_hashes.add(str(manifest_hash))
        default_sources.append(ClaimSource(entry_id=entry_id, spans=[]))

    return normalized_ids, sorted(manifest_hashes), default_sources


def _derived_microfacts_path(root: Path, day: str) -> Path:
    return root / "derived" / "microfacts" / f"{day}.yaml"


def run_facts(
    date: str,
    *,
    timeout: float,
    retries: int,
    progress: bool,
    claim_models: Sequence[ClaimAtom],
    build_claim_preview: Callable[
        [Sequence[ClaimProposal], Sequence[ClaimAtom], str], ProfileUpdatePreview | None
    ],
) -> tuple[ProfileUpdatePreview | None, Path]:
    """Generate daily micro-facts and return the preview plus output path."""
    root = Path.cwd()
    entries = _load_normalized_entries(root, date)
    if not entries:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    timeout_value = _validate_timeout(timeout)
    _log_entry_progress(f"Extracting micro-facts for {date}", entries, progress)

    config = _load_config(root)
    manifest_entries = _load_manifest(_manifest_path(root))
    manifest_index = _manifest_by_id(manifest_entries)
    context = _characterization_context(entries, manifest_index)
    use_fake_llm = _use_fake_llm()

    def request_microfacts() -> MicroFactsFile:
        return cast(
            MicroFactsFile,
            _invoke_structured_llm(
                "prompts/extract_facts.md",
                {"date": date, "entries_json": _json_block(_entries_to_payload(entries))},
                response_model=MicroFactsFile,
                agent_name="aijournal-facts",
                config=config,
                timeout=timeout_value,
                max_attempts=max(1, retries + 1),
                retry_message=(
                    "Return JSON with keys `facts` and `claim_proposals` only. "
                    "Each fact must include id, statement, confidence, evidence and dates."
                ),
            ),
        )

    try:
        facts_data = facts_pipeline.generate_microfacts(
            entries,
            date,
            use_fake_llm=use_fake_llm,
            structured_call=_structured_call_with_retry,
            request_factory=request_microfacts,
            retries=retries,
            context=context,
            manifest_index=manifest_index,
        )
    except LLMResponseError as exc:  # pragma: no cover - runtime dependent
        typer.secho(f"Facts extraction failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    summary_meta = _build_meta("prompts/extract_facts.md", config=config)
    preview = build_claim_preview(
        facts_data.claim_proposals,
        [claim.model_copy(deep=True) for claim in claim_models],
        time_utils.format_timestamp(time_utils.now()),
    )
    facts_data.preview = preview

    facts_path = _derived_microfacts_path(root, date)
    artifact_meta = _artifact_meta_from_summary_meta(summary_meta)
    save_artifact(
        facts_path,
        Artifact[MicroFactsFile](
            kind=ArtifactKind.MICROFACTS_DAILY,
            meta=artifact_meta,
            data=facts_data,
        ),
    )
    return preview, facts_path


def _artifact_meta_from_summary_meta(meta: SummaryMeta) -> ArtifactMeta:
    created_at = meta.created_at or time_utils.format_timestamp(time_utils.now())
    return ArtifactMeta(
        created_at=created_at,
        model=meta.llm_model,
        prompt_path=meta.prompt_path,
        prompt_hash=meta.prompt_hash,
    )
