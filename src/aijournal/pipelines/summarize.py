"""Pipeline orchestration for daily summary generation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, cast

from aijournal.fakes import fake_summarize
from aijournal.models import DailySummary, NormalizedEntry

StructuredCall = Callable[..., Any]
ResponseFactory = Callable[[], DailySummary]


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
    structured_call: StructuredCall,
    request_factory: ResponseFactory,
    retries: int,
) -> DailySummary:
    """Produce a `DailySummary` for the given date."""

    def fallback_model() -> DailySummary:
        return fake_summarize(entries, date, todo_builder=_todo_from_entries)

    if use_fake_llm:
        return fallback_model()

    response = cast(
        DailySummary,
        structured_call(request_factory, retries=retries, label=f"summarize {date}"),
    )

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


__all__ = ["generate_summary"]
