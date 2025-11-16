from __future__ import annotations

from aijournal.services.capture.stages import stage0_persist as stage0


def test_missing_summary_uses_first_paragraph() -> None:
    body = (
        "First line with extra   spaces.\nStill first paragraph.\n\nSecond paragraph ignores this."
    )
    summary = stage0._derive_summary_text(None, body)
    assert summary == "First line with extra spaces. Still first paragraph."


def test_existing_summary_remains_unchanged() -> None:
    summary = stage0._derive_summary_text("Custom summary", "Body text")
    assert summary == "Custom summary"


def test_long_summary_truncates_with_ellipsis() -> None:
    body = "Lorem ipsum " * 50  # >400 chars
    summary = stage0._derive_summary_text(None, body, max_chars=100)
    assert summary.endswith("...")
    assert len(summary) <= 103
