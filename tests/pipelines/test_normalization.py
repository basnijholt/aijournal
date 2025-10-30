from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from aijournal.ingest_agent import IngestResult, IngestSection
from aijournal.pipelines import normalization

TIMESTAMP = "2024-01-02T03:04:05Z"


def test_merge_sections_deduplicates_and_falls_back() -> None:
    primary = [
        IngestSection(heading="Overview", level=1),
        IngestSection(heading="overview", level=2, summary="duplicate heading"),
        IngestSection(heading="Plan", level=2),
    ]
    fallback = [
        {"heading": "Plan"},  # duplicate should be ignored
        {"heading": "Details", "level": 3},
    ]

    merged = normalization.merge_sections(primary, fallback, title="Entry Title", limit=3)

    headings = [section["heading"] for section in merged]
    assert headings == ["Overview", "Plan", "Details"]


def test_normalized_from_structured_sanitizes_and_merges() -> None:
    structured = IngestResult(
        entry_id="focus-session",
        created_at=datetime(2024, 1, 2, 12, 30, tzinfo=UTC),
        title="Deep Work Session",
        tags=["Focus", "deep work"],
        sections=[
            IngestSection(heading="Highlights", level=2, summary="Key outcomes"),
            IngestSection(heading="Next Steps", level=2, summary="Follow-ups"),
        ],
        summary='First sentence. Second sentence? Third sentence! ,"entry_id":"noise"',
    )

    normalized, date_str = normalization.normalized_from_structured(
        structured,
        source_path="workspace/data/journal/entry.md",
        root=Path("/tmp"),
        digest="deadbeefcafebabe",
        source_type="markdown",
        fallback_sections=[{"heading": "Fallback"}],
        fallback_tags=["Routine"],
        fallback_summary="Fallback summary.",
    )

    assert date_str == "2024-01-02"
    assert normalized["id"].startswith("2024-01-02-focus-session")
    assert normalized["created_at"] == "2024-01-02T12:30:00Z"
    assert normalized["source_path"] == "workspace/data/journal/entry.md"
    # Tags should be slugified and deduplicated while preserving order
    assert normalized["tags"] == ["focus", "deep-work", "routine"]
    assert normalized["sections"][0]["heading"] == "Highlights"
    assert normalized["sections"][1]["heading"] == "Next Steps"
    assert "summary" in normalized
    assert normalized["summary"] == "First sentence. Second sentence?"


def test_normalize_claim_atom_generates_defaults() -> None:
    payload = {
        "statement": "Enjoy morning walks",
        "confidence": 0.4,
        "scope": {"context": [" outdoors "]},
    }

    claim = normalization.normalize_claim_atom(payload, timestamp=TIMESTAMP)

    assert claim.id.startswith("preference.enjoy-morning-walks")
    assert claim.scope.context == ["outdoors"]
    assert claim.strength == pytest.approx(0.4)
    assert claim.provenance.first_seen == "2024-01-02"
    assert claim.provenance.last_updated == TIMESTAMP
