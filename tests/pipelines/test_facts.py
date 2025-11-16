from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from aijournal.common.app_config import AppConfig
from aijournal.domain.changes import ClaimProposal
from aijournal.domain.claims import ClaimSource, Scope
from aijournal.domain.evidence import SourceRef
from aijournal.domain.facts import MicroFact, MicroFactsFile
from aijournal.domain.journal import NormalizedEntry
from aijournal.models.authoritative import ManifestEntry
from aijournal.pipelines import facts as facts_pipeline
from aijournal.services.microfacts import MicrofactIndex, MicrofactRecord

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _normalized_entry(entry_id: str) -> NormalizedEntry:
    return NormalizedEntry(
        id=entry_id,
        created_at="2024-01-02T09:00:00Z",
        source_path=f"data/journal/{entry_id}.md",
        title="Deep Work Session",
        tags=["focus"],
        sections=[],
        source_hash="hash-1",
    )


def _characterization_context(
    entry_id: str,
) -> tuple[list[str], list[str], list[ClaimSource]]:
    return ([entry_id], ["manifest-1"], [ClaimSource(entry_id=entry_id, spans=[])])


def test_generate_microfacts_uses_fake_pipeline() -> None:
    entry = _normalized_entry("entry-1")
    context = _characterization_context("entry-1")

    result = facts_pipeline.generate_microfacts(
        [entry],
        "2024-01-02",
        use_fake_llm=True,
        llm_microfacts=None,
        context=context,
        manifest_index={},
        microfact_index=None,
    )

    assert result.facts  # fake generator returns deterministic facts


def test_generate_microfacts_merges_llm_and_derived(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _normalized_entry("entry-1")
    context = _characterization_context("entry-1")
    manifest_entry = ManifestEntry(
        hash="manifest-1",
        path="notes.md",
        normalized="normalized.yaml",
        source_type="markdown",
        ingested_at="2024-01-02T09:00:00Z",
        created_at="2024-01-02T08:30:00Z",
        id="entry-1",
    )

    response = MicroFactsFile(
        facts=[
            MicroFact(
                id="fact-1",
                statement="Completed focus block",
                confidence=0.9,
                evidence=SourceRef(entry_id="entry-1", spans=[]),
                first_seen="2024-01-02",
                last_seen="2024-01-02",
            ),
        ],
        claim_proposals=[
            ClaimProposal(
                type="preference",
                subject="fact-1",
                predicate="insight",
                value="Completed focus block",
                statement="Completed focus block",
                scope=Scope(),
                strength=0.8,
                status="tentative",
                method="inferred",
                user_verified=False,
                review_after_days=90,
                normalized_ids=["entry-1"],
                manifest_hashes=["manifest-1"],
                evidence=[SourceRef(entry_id="entry-1", spans=[])],
                rationale=None,
            ),
        ],
    )

    fixed_now = datetime(2024, 1, 2, 10, 0, tzinfo=UTC)
    monkeypatch.setattr("aijournal.utils.time.now", lambda: fixed_now)

    result = facts_pipeline.generate_microfacts(
        [entry],
        "2024-01-02",
        use_fake_llm=False,
        llm_microfacts=response,
        context=context,
        manifest_index={"entry-1": manifest_entry},
        microfact_index=None,
    )

    assert len(result.facts) == 1
    # LLM claim duplicates derived claim, ensure deduplicated
    assert len(result.claim_proposals) == 1
    proposal = result.claim_proposals[0]
    assert isinstance(proposal, ClaimProposal)
    assert proposal.statement == "Completed focus block"
    assert proposal.normalized_ids == ["entry-1"]
    assert proposal.manifest_hashes == ["manifest-1"]
    assert proposal.evidence == [SourceRef(entry_id="entry-1", spans=[])]


def _run_custom_microfacts(
    entry: NormalizedEntry,
    *,
    date: str,
    statement: str,
    microfact_index: MicrofactIndex,
) -> MicroFactsFile:
    context = _characterization_context(entry.id or "entry-1")

    return facts_pipeline.generate_microfacts(
        [entry],
        date,
        use_fake_llm=False,
        llm_microfacts=MicroFactsFile(
            facts=[
                MicroFact(
                    id=f"{entry.id}-fact",
                    statement=statement,
                    confidence=0.8,
                    evidence=SourceRef(entry_id=entry.id, spans=[]),
                    first_seen=date,
                    last_seen=date,
                ),
            ],
            claim_proposals=[],
        ),
        context=context,
        manifest_index={},
        microfact_index=microfact_index,
    )


def test_generate_microfacts_consolidates_repeated_statements(tmp_path: Path) -> None:
    config = AppConfig()
    microfact_index = MicrofactIndex(tmp_path, config, fake_mode=True)
    entry_day1 = _normalized_entry("entry-1")
    entry_day2 = _normalized_entry("entry-2")

    result_day1 = _run_custom_microfacts(
        entry_day1,
        date="2024-01-02",
        statement="Morning deep work focus block",
        microfact_index=microfact_index,
    )
    result_day2 = _run_custom_microfacts(
        entry_day2,
        date="2024-01-03",
        statement="Morning deep work focus block",
        microfact_index=microfact_index,
    )

    assert result_day1.facts[0].first_seen == "2024-01-02"
    assert result_day2.facts[0].first_seen == "2024-01-02"
    assert result_day2.facts[0].last_seen == "2024-01-03"

    matches = microfact_index.query_similar("Morning deep work focus block", top_k=1)
    assert matches
    record = MicrofactRecord.from_match(matches[0])
    assert record is not None
    assert record.observation_count == 2


def test_generate_microfacts_creates_new_records_for_unique_statements(tmp_path: Path) -> None:
    config = AppConfig()
    microfact_index = MicrofactIndex(tmp_path, config, fake_mode=True)
    entry = _normalized_entry("entry-1")

    _run_custom_microfacts(
        entry,
        date="2024-01-02",
        statement="Morning deep work focus block",
        microfact_index=microfact_index,
    )
    _run_custom_microfacts(
        entry,
        date="2024-01-03",
        statement="Evening recovery walk",
        microfact_index=microfact_index,
    )

    match_focus = microfact_index.query_similar("Morning deep work focus block", top_k=1)
    match_walk = microfact_index.query_similar("Evening recovery walk", top_k=1)
    assert match_focus
    assert match_walk
    focus_record = MicrofactRecord.from_match(match_focus[0])
    walk_record = MicrofactRecord.from_match(match_walk[0])
    assert focus_record is not None
    assert walk_record is not None
    assert focus_record.uid != walk_record.uid
