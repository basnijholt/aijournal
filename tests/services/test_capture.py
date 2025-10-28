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


def test_run_capture_records_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")
    monkeypatch.setattr(
        "aijournal.utils.time.now",
        lambda: datetime(2025, 10, 28, 9, 0, tzinfo=UTC),
    )
    monkeypatch.chdir(tmp_path)

    stage_calls: list[tuple[str, str]] = []
    profile_apply_calls: list[str] = []
    review_calls: list[Path] = []

    def _ensure_file(path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def fake_run_summarize(date: str, *, timeout: float, retries: int, progress: bool) -> Path:
        del timeout, retries, progress
        stage_calls.append(("summarize", date))
        return _ensure_file(tmp_path / "derived" / "summaries" / f"{date}.yaml", "summary")

    def fake_run_facts(
        date: str,
        *,
        timeout: float,
        retries: int,
        progress: bool,
        claim_models,
        build_claim_preview,
    ) -> tuple[None, Path]:
        del timeout, retries, progress, claim_models, build_claim_preview
        stage_calls.append(("facts", date))
        path = _ensure_file(tmp_path / "derived" / "microfacts" / f"{date}.yaml", "facts")
        return None, path

    def fake_run_profile_suggest(
        date: str, *, timeout: float, retries: int, progress: bool
    ) -> Path:
        del timeout, retries, progress
        stage_calls.append(("profile_suggest", date))
        return _ensure_file(
            tmp_path / "derived" / "profile_suggestions" / f"{date}.yaml",
            "suggest",
        )

    def fake_run_profile_apply(
        date: str,
        *,
        suggestions_path: Path | None,
        auto_confirm: bool,
    ) -> str:
        del suggestions_path, auto_confirm
        profile_apply_calls.append(date)
        return "Applied"

    def fake_run_characterize(
        date: str,
        *,
        timeout: float,
        retries: int,
        progress: bool,
        build_claim_preview,
    ) -> Path:
        del timeout, retries, progress, build_claim_preview
        stage_calls.append(("characterize", date))
        return _ensure_file(
            tmp_path / "derived" / "pending" / "profile_updates" / f"{date}-batch.yaml",
            "batch",
        )

    def fake_apply_batch(root: Path, batch_path: Path) -> bool:
        del root
        review_calls.append(batch_path)
        return True

    monkeypatch.setattr(
        "aijournal.services.capture.run_summarize_command",
        fake_run_summarize,
    )
    monkeypatch.setattr("aijournal.services.capture.run_facts", fake_run_facts)
    monkeypatch.setattr(
        "aijournal.services.capture.run_profile_suggest",
        fake_run_profile_suggest,
    )
    monkeypatch.setattr(
        "aijournal.services.capture.run_profile_apply",
        fake_run_profile_apply,
    )
    monkeypatch.setattr(
        "aijournal.services.capture.run_characterize",
        fake_run_characterize,
    )
    monkeypatch.setattr(
        "aijournal.services.capture._apply_profile_update_batch",
        fake_apply_batch,
    )
    monkeypatch.setattr(
        "aijournal.services.capture._load_profile_components",
        lambda root: (None, []),
    )

    inputs = CaptureInput(source="stdin", text="Hello capture", title="Capture")
    result = run_capture(inputs)

    assert result.run_id.startswith("capture-")
    for key in [
        "persist",
        "normalize",
        "derive.summarize",
        "derive.extract_facts",
        "derive.profile_suggest",
        "derive.profile_apply",
        "derive.characterize",
        "derive.review",
    ]:
        assert key in result.durations_ms
        assert result.durations_ms[key] >= 0

    assert result.artifacts_changed.get("summaries") == 1
    assert result.artifacts_changed.get("microfacts") == 1
    assert result.artifacts_changed.get("profile_suggestions") == 1
    assert result.artifacts_changed.get("characterize") == 1
    assert result.artifacts_changed.get("profile") == 2

    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.markdown_path is not None
    assert (tmp_path / entry.markdown_path).exists()
    assert entry.normalized_path is not None
    assert (tmp_path / entry.normalized_path).exists()

    assert profile_apply_calls == [entry.date]
    assert review_calls
    assert stage_calls[0][0] == "summarize"

    manifest_path = tmp_path / "data" / "manifest" / "ingested.yaml"
    assert manifest_path.exists()


def test_run_capture_requires_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    inputs = CaptureInput(source="stdin")
    with pytest.raises(ValueError):
        run_capture(inputs)


def test_run_capture_review_mode_skips_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")
    monkeypatch.setattr(
        "aijournal.utils.time.now",
        lambda: datetime(2025, 10, 28, 10, 0, tzinfo=UTC),
    )
    monkeypatch.chdir(tmp_path)

    def _ensure_file(path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    monkeypatch.setattr(
        "aijournal.services.capture.run_summarize_command",
        lambda date, *, timeout, retries, progress: _ensure_file(
            tmp_path / "derived" / "summaries" / f"{date}.yaml", "summary"
        ),
    )

    monkeypatch.setattr(
        "aijournal.services.capture.run_facts",
        lambda date, *, timeout, retries, progress, claim_models, build_claim_preview: (
            None,
            _ensure_file(tmp_path / "derived" / "microfacts" / f"{date}.yaml", "facts"),
        ),
    )

    monkeypatch.setattr(
        "aijournal.services.capture.run_profile_suggest",
        lambda date, *, timeout, retries, progress: _ensure_file(
            tmp_path / "derived" / "profile_suggestions" / f"{date}.yaml",
            "suggest",
        ),
    )

    monkeypatch.setattr(
        "aijournal.services.capture.run_characterize",
        lambda date, *, timeout, retries, progress, build_claim_preview: _ensure_file(
            tmp_path / "derived" / "pending" / "profile_updates" / f"{date}-batch.yaml",
            "batch",
        ),
    )

    profile_apply_calls: list[str] = []
    review_calls: list[Path] = []

    monkeypatch.setattr(
        "aijournal.services.capture.run_profile_apply",
        lambda *args, **kwargs: profile_apply_calls.append("called"),
    )
    monkeypatch.setattr(
        "aijournal.services.capture._apply_profile_update_batch",
        lambda root, path: review_calls.append(path) or True,
    )
    monkeypatch.setattr(
        "aijournal.services.capture._load_profile_components",
        lambda root: (None, []),
    )

    inputs = CaptureInput(
        source="stdin",
        text="Hello capture",
        title="Capture",
        apply_profile="review",
    )
    result = run_capture(inputs)

    assert "derive.profile_apply" not in result.durations_ms
    assert "derive.review" not in result.durations_ms
    assert not profile_apply_calls
    assert not review_calls


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
