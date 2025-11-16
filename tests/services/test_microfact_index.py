"""Tests for the Chroma-backed microfact index service."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from aijournal.common.app_config import AppConfig
from aijournal.domain.evidence import SourceRef
from aijournal.domain.facts import MicroFact, MicroFactsFile
from aijournal.io.yaml_io import write_yaml_model
from aijournal.services.microfacts import MicrofactIndex, MicrofactRecord

if TYPE_CHECKING:
    from pathlib import Path


def _make_record(statement: str, *, day: str = "2025-11-14") -> MicrofactRecord:
    fact = MicroFact(
        id=f"fact-{day}",
        statement=statement,
        confidence=0.7,
        evidence=SourceRef(entry_id=f"entry-{day}", spans=[]),
        first_seen=day,
        last_seen=day,
    )
    return MicrofactRecord.from_microfact(
        day=day,
        fact=fact,
        domain="journal",
        contexts=["focus"],
    )


def test_upsert_and_query_returns_matches(tmp_path: Path) -> None:
    config = AppConfig()
    index = MicrofactIndex(tmp_path, config, fake_mode=True)
    record = _make_record("Morning deep work focus block")
    index.upsert([record])

    matches = index.query_similar("deep work block", top_k=1)

    assert matches, "Expected at least one microfact match"
    match_record = MicrofactRecord.from_match(matches[0])
    assert match_record is not None
    assert match_record.uid == record.uid
    assert "deep work" in matches[0].statement.lower()
    assert match_record.source_fact_ids == record.source_fact_ids


def test_reset_clears_existing_entries(tmp_path: Path) -> None:
    index = MicrofactIndex(tmp_path, AppConfig(), fake_mode=True)
    index.upsert([_make_record("Morning stretching routine")])
    assert index.query_similar("stretching", top_k=1)

    index.reset()

    assert not index.query_similar("stretching", top_k=1)


def test_rebuild_from_daily_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path
    derived_microfacts = workspace / "derived" / "microfacts"
    derived_microfacts.mkdir(parents=True, exist_ok=True)
    day_path = derived_microfacts / "2025-11-10.yaml"
    fact = MicroFact(
        id="focus",
        statement="Completed a two-hour deep work sprint",
        confidence=0.9,
        evidence=SourceRef(entry_id="entry-1", spans=[]),
        first_seen="2025-11-10",
        last_seen="2025-11-10",
    )
    write_yaml_model(day_path, MicroFactsFile(facts=[fact]))

    index = MicrofactIndex(workspace, AppConfig(), fake_mode=True)
    result = index.rebuild_from_daily_artifacts()

    assert len(result.facts) == 1
    assert result.stats
    assert result.stats[0].processed == 1
    matches = index.query_similar("deep work sprint", top_k=1)
    assert matches
    reconstructed = MicrofactRecord.from_match(matches[0])
    assert reconstructed is not None
    assert reconstructed.first_seen == "2025-11-10"


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"projects": ["focus"]},
    ],
)
def test_metadata_filters_none_values(extra: dict[str, object]) -> None:
    record = MicrofactRecord(
        uid="consolidated",
        statement="Planning lunch prep",
        canonical_statement="planning lunch prep",
        confidence=0.4,
        first_seen="2025-11-14",
        last_seen="2025-11-14",
        domain=None,
        contexts=[],
        observation_count=1,
        evidence_entries=[],
        source_fact_ids=["fact"],
        extra=extra,
    )
    metadata = record.metadata
    assert metadata["canonical_statement"] == "planning lunch prep"
    if extra:
        assert json.loads(metadata["projects"]) == ["focus"]
