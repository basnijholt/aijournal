"""Domain models for evidence spans and source references."""

from __future__ import annotations

from pydantic import Field

from aijournal.common.base import StrictModel


class Span(StrictModel):
    """Detailed pointer back into a source document."""

    type: str
    index: int | None = None
    start: int | None = None
    end: int | None = None
    text: str | None = None


class SourceRef(StrictModel):
    """Reference to a normalized entry and the supporting spans."""

    entry_id: str
    spans: list[Span] = Field(default_factory=list)


def redact_source_text(source: SourceRef) -> SourceRef:
    """Return a copy of ``source`` with all span text removed."""
    redacted_spans = [span.model_copy(update={"text": None}) for span in source.spans]
    return source.model_copy(update={"spans": redacted_spans})
