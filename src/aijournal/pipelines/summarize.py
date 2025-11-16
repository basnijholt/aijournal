"""Pipeline orchestration for daily summary generation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from aijournal.domain.facts import DailySummary
from aijournal.domain.journal import NormalizedEntry
from aijournal.fakes import fake_summarize


def _todo_from_entries(entries: Sequence[NormalizedEntry]) -> list[str]:
    todos: list[str] = []
    for entry in entries[:3]:
        title = entry.title or entry.id or "entry"
        todos.append(f"Review follow-ups from {title}")
    return todos or ["Capture explicit next actions in tomorrow's entry."]


def generate_summary(
    entries: Sequence[NormalizedEntry],
    date: str,
    *,
    use_fake_llm: bool,
    llm_summary: DailySummary | None,
) -> DailySummary:
    """Produce a `DailySummary` for the given date."""

    def fallback_model() -> DailySummary:
        return fake_summarize(entries, date, todo_builder=_todo_from_entries)

    if use_fake_llm:
        return fallback_model()

    if llm_summary is None:
        msg = "llm_summary must be provided when fake mode is disabled"
        raise ValueError(msg)

    response = cast(DailySummary, llm_summary)

    bullets = [item for item in response.bullets if item]
    highlights = [item for item in response.highlights if item]
    todo_candidates = [item for item in response.todo_candidates if item]

    if not bullets:
        fallback = fallback_model()
        bullets = fallback.bullets
        if not highlights:
            highlights = fallback.highlights
        if not todo_candidates:
            todo_candidates = fallback.todo_candidates

    if not highlights:
        highlights = bullets[:3]
    if not todo_candidates:
        todo_candidates = _todo_from_entries(entries)

    day = response.day or date

    return DailySummary(
        day=day,
        bullets=bullets,
        highlights=highlights,
        todo_candidates=todo_candidates,
    )
