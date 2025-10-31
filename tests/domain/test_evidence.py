from __future__ import annotations

from aijournal.domain.evidence import SourceRef, Span, redact_source_text


def test_span_allows_text_field() -> None:
    span = Span(type="excerpt", text="sample excerpt", index=0)
    assert span.text == "sample excerpt"


def test_redact_source_text_strips_span_text() -> None:
    original = SourceRef(
        entry_id="entry-123",
        spans=[
            Span(type="excerpt", text="keep private", index=0),
            Span(type="highlight", text="another", index=1),
        ],
    )

    redacted = redact_source_text(original)

    # Ensure we returned a new object and no span retains text
    assert redacted is not original
    assert all(span.text is None for span in redacted.spans)
    # Original instance is unchanged
    assert original.spans[0].text == "keep private"
