"""Helpers for loading and filtering consolidated microfacts snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from aijournal.domain.facts import ConsolidatedMicroFact, ConsolidatedMicrofactsFile
from aijournal.io.artifacts import load_artifact_data

if TYPE_CHECKING:
    from aijournal.common.app_config import AppConfig


def _consolidated_path(workspace: Path, config: AppConfig) -> Path:
    derived = Path(config.paths.derived)
    if not derived.is_absolute():
        derived = workspace / derived
    return derived / "microfacts" / "consolidated.yaml"


def load_consolidated_microfacts(
    workspace: Path,
    config: AppConfig,
) -> ConsolidatedMicrofactsFile | None:
    """Return the consolidated snapshot if it exists and validates."""
    path = _consolidated_path(workspace, config)
    if not path.exists():
        return None
    try:
        return load_artifact_data(path, ConsolidatedMicrofactsFile)
    except ValidationError:
        return None


def select_recurring_facts(
    snapshot: ConsolidatedMicrofactsFile,
    *,
    min_observations: int = 2,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return the strongest recurring facts for prompt context."""
    candidates: list[ConsolidatedMicroFact] = [
        fact for fact in snapshot.facts if fact.observation_count >= min_observations
    ]
    sorted_facts = sorted(
        candidates,
        key=lambda fact: (-fact.observation_count, fact.last_seen, fact.id),
    )[:limit]
    return [
        {
            "statement": fact.statement,
            "observation_count": fact.observation_count,
            "first_seen": fact.first_seen,
            "last_seen": fact.last_seen,
            "contexts": fact.contexts,
            "evidence_entries": fact.evidence_entries,
        }
        for fact in sorted_facts
    ]
