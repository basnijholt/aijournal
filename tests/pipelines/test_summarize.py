from __future__ import annotations

from collections.abc import Callable

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
        raise AssertionError("request_factory should not be invoked for fake flows")

    def structured_call(  # pragma: no cover - should not run
        func: Callable[[], DailySummary],
        *,
        retries: int,
        label: str,
    ) -> DailySummary:
        raise AssertionError(f"structured_call called unexpectedly ({label=}, {retries=})")

    summary_result = summarize.generate_summary(
        entries,
        "2024-01-02",
        use_fake_llm=True,
        structured_call=structured_call,
        request_factory=request_factory,
        retries=2,
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

    def request_factory() -> DailySummary:
        return response

    call_args: dict[str, object] = {}

    def structured_call(
        func: Callable[[], DailySummary],
        *,
        retries: int,
        label: str,
    ) -> DailySummary:
        call_args["retries"] = retries
        call_args["label"] = label
        return func()

    summary_result = summarize.generate_summary(
        entries,
        "2024-01-02",
        use_fake_llm=False,
        structured_call=structured_call,
        request_factory=request_factory,
        retries=3,
    )

    assert call_args == {"retries": 3, "label": "summarize 2024-01-02"}
    assert summary_result.day == "2024-01-02"
    assert summary_result.bullets == ["Refined insight"]
    assert summary_result.highlights == ["Refined insight"]
    assert summary_result.todo_candidates == ["Review notes"]
