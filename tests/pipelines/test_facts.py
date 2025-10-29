from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from aijournal.domain.changes import ClaimAtomInput
from aijournal.domain.evidence import SourceRef
from aijournal.models import (
    ClaimProposal,
    ClaimSource,
    ManifestEntry,
    MicroFact,
    MicroFactsFile,
    NormalizedEntry,
    Scope,
)
from aijournal.pipelines import facts as facts_pipeline


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


def test_generate_microfacts_uses_fake_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _normalized_entry("entry-1")
    context = _characterization_context("entry-1")

    called: dict[str, bool] = {"structured": False}

    def structured_call(  # pragma: no cover - fake mode should skip
        func: Callable[[], MicroFactsFile],
        *,
        retries: int,
        label: str,
    ) -> MicroFactsFile:
        called["structured"] = True
        return func()

    def request_factory() -> MicroFactsFile:  # pragma: no cover - fake mode should skip
        raise AssertionError("request_factory should not run in fake mode")

    result = facts_pipeline.generate_microfacts(
        [entry],
        "2024-01-02",
        use_fake_llm=True,
        structured_call=structured_call,
        request_factory=request_factory,
        retries=2,
        context=context,
        manifest_index={},
    )

    assert not called["structured"]
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
            )
        ],
        claim_proposals=[
            ClaimProposal(
                claim=ClaimAtomInput(
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
                ),
                normalized_ids=["entry-1"],
                manifest_hashes=["manifest-1"],
                evidence=[SourceRef(entry_id="entry-1", spans=[])],
                rationale=None,
            )
        ],
    )

    call_args: dict[str, object] = {}

    def structured_call(
        func: Callable[[], MicroFactsFile],
        *,
        retries: int,
        label: str,
    ) -> MicroFactsFile:
        call_args["retries"] = retries
        call_args["label"] = label
        return func()

    def request_factory() -> MicroFactsFile:
        return response

    fixed_now = datetime(2024, 1, 2, 10, 0, tzinfo=UTC)
    monkeypatch.setattr("aijournal.utils.time.now", lambda: fixed_now)

    result = facts_pipeline.generate_microfacts(
        [entry],
        "2024-01-02",
        use_fake_llm=False,
        structured_call=structured_call,
        request_factory=request_factory,
        retries=3,
        context=context,
        manifest_index={"entry-1": manifest_entry},
    )

    assert call_args == {"retries": 3, "label": "facts 2024-01-02"}
    assert len(result.facts) == 1
    # LLM claim duplicates derived claim, ensure deduplicated
    assert len(result.claim_proposals) == 1
    proposal = result.claim_proposals[0]
    assert isinstance(proposal, ClaimProposal)
    assert isinstance(proposal.claim, ClaimAtomInput)
    assert proposal.claim.statement == "Completed focus block"
    assert proposal.normalized_ids == ["entry-1"]
    assert proposal.manifest_hashes == ["manifest-1"]
    assert proposal.evidence == [SourceRef(entry_id="entry-1", spans=[])]
