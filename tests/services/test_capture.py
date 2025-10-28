"""Tests for the capture service scaffolding."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from aijournal.models import ManifestEntry
from aijournal.services.capture import (
    CaptureInput,
    EntryResult,
    _persist_file_entry,
    _persist_text_entry,
    normalize_entries,
    run_capture,
)


def test_capture_input_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aijournal.utils.time.now",
        lambda: datetime(2025, 10, 28, 12, 0, tzinfo=UTC),
    )
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


def test_persist_text_writes_markdown_and_normalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aijournal.utils.time.now",
        lambda: datetime(2025, 10, 28, 9, 0, tzinfo=UTC),
    )
    inputs = CaptureInput(source="stdin", text="Hello capture", title="My Entry")
    manifest: list[ManifestEntry] = []
    result = _persist_text_entry(inputs, tmp_path, manifest)

    assert result.slug.startswith("2025-10-28")
    assert result.markdown_path
    assert result.normalized_path
    markdown = tmp_path / result.markdown_path
    normalized = tmp_path / result.normalized_path
    assert markdown.exists()
    assert normalized.exists()
    assert "Hello capture" in markdown.read_text(encoding="utf-8")
    normalized_payload = yaml.safe_load(normalized.read_text(encoding="utf-8"))
    assert normalized_payload["summary"] == "Hello capture"
    assert manifest  # manifest entry recorded

    normalized_path = tmp_path / result.normalized_path
    normalized_path.unlink()
    copy = result.model_copy(update={"changed": True})
    counts = normalize_entries([copy], tmp_path)
    assert counts["normalized"] == 1
    assert normalized_path.exists()


def test_persist_file_skips_duplicate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aijournal.utils.time.now",
        lambda: datetime(2025, 10, 28, 9, 0, tzinfo=UTC),
    )
    entry_path = tmp_path / "entry.md"
    entry_path.write_text(
        "---\nid: custom-slug\ncreated_at: 2025-10-27\ntitle: Sample\n---\nBody", encoding="utf-8"
    )

    inputs = CaptureInput(source="file", paths=[str(entry_path)])
    manifest: list[ManifestEntry] = []
    first = _persist_file_entry(inputs, tmp_path, manifest)
    assert first.changed is True
    second = _persist_file_entry(inputs, tmp_path, manifest)
    assert second.deduped is True

    counts = normalize_entries([second], tmp_path)
    # Already normalized via first persist; second should trigger no rewrite.
    assert counts["normalized"] == 0
