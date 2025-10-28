"""Tests for the capture service scaffolding."""

from __future__ import annotations

import pytest

from aijournal.services.capture import CaptureInput, EntryResult, run_capture


def test_capture_input_defaults() -> None:
    payload = CaptureInput(source="stdin")
    assert payload.source_type == "journal"
    assert payload.progress is True
    assert payload.dry_run is False
    assert payload.rebuild == "auto"


def test_entry_result_defaults() -> None:
    entry = EntryResult(date="2025-10-28", slug="test-entry")
    assert entry.deduped is False
    assert entry.changed is False
    assert entry.warnings == []


def test_run_capture_stub_raises() -> None:
    inputs = CaptureInput(source="stdin")
    with pytest.raises(NotImplementedError):
        run_capture(inputs)
