from __future__ import annotations

from aijournal.domain.facts import DailySummary
from aijournal.domain.journal import NormalizedEntry
from aijournal.models.authoritative import JournalSection
from aijournal.pipelines import summarize


def _normalized_entry(entry_id: str, title: str) -> NormalizedEntry:
    return NormalizedEntry(
        id=entry_id,
        created_at="2024-01-02T09:00:00Z",
        source_path=f"data/journal/{entry_id}.md",
        title=title,
        tags=["focus"],
        sections=[JournalSection(heading="Highlights", level=2)],
    )


def test_generate_summary_uses_fake_path_when_requested() -> None:
    entries = [_normalized_entry("entry-1", "Deep Work")]

    def request_factory() -> DailySummary:  # pragma: no cover - should not run
        msg = "request_factory should not be invoked for fake flows"
        raise AssertionError(msg)

    summary_result = summarize.generate_summary(
        entries,
        "2024-01-02",
        use_fake_llm=True,
        llm_summary=None,
    )

    assert summary_result.day == "2024-01-02"
    assert summary_result.bullets[0].startswith("Deep Work")
    assert summary_result.todo_candidates


def test_generate_summary_merges_llm_results_with_fallback() -> None:
    entries = [_normalized_entry("entry-1", "Deep Work")]
    response = DailySummary(
        day="",
        bullets=["Refined insight", ""],
        highlights=[],
        todo_candidates=["", "Review notes"],
    )

    summary_result = summarize.generate_summary(
        entries,
        "2024-01-02",
        use_fake_llm=False,
        llm_summary=response,
    )
    assert summary_result.day == "2024-01-02"
    assert summary_result.bullets == ["Refined insight"]
    assert summary_result.highlights == ["Refined insight"]
    assert summary_result.todo_candidates == ["Review notes"]
