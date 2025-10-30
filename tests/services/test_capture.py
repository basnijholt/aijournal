"""Tests for the capture service scaffolding."""

from __future__ import annotations

import json
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from tests.helpers import make_claim_atom

from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.changes import ProfileUpdateProposals
from aijournal.domain.facts import (
    DailySummary,
    FactEvidence,
    FactEvidenceSpan,
    MicroFact,
    MicroFactsFile,
    SummaryMeta,
)
from aijournal.io.artifacts import save_artifact
from aijournal.models.authoritative import ManifestEntry
from aijournal.models.derived import (
    ProfileSuggestions,
    ProfileSuggestionUpsert,
    ProfileUpdateBatch,
    ProfileUpdateInput,
    ProfileUpdatePreview,
)
from aijournal.services.capture import (
    CaptureInput,
    normalize_entries,
    run_capture,
)
from aijournal.services.capture.stages.stage0_persist import (
    EntryResult,
    _persist_file_entry,
    _persist_text_entry,
)
from aijournal.services.capture.utils import discover_markdown_files


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
    index_rebuild_calls: list[tuple[str | None, int | None]] = []
    index_tail_calls: list[tuple[str | None, int, int | None]] = []
    persona_build_calls: list[tuple[dict[str, object], list[object]]] = []
    persona_state_calls: list[Path] = []
    pack_calls: list[tuple[str, Path]] = []

    def _ensure_file(path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _write_summary_artifact(path: Path, day: str) -> Path:
        meta = SummaryMeta(
            llm_model="fake-ollama",
            prompt_path="prompts/summarize_day.md",
            prompt_hash="fake",
            created_at=f"{day}T09:00:00Z",
        )
        summary = DailySummary(
            day=day,
            bullets=["Captured entry"],
            highlights=["Highlight"],
            todo_candidates=[],
        )
        artifact = Artifact[DailySummary](
            kind=ArtifactKind.SUMMARY_DAILY,
            meta=ArtifactMeta(
                created_at=meta.created_at,
                model=meta.llm_model,
                prompt_path=meta.prompt_path,
                prompt_hash=meta.prompt_hash,
            ),
            data=summary,
        )
        save_artifact(path, artifact)
        return path

    def _write_microfacts_artifact(path: Path, day: str) -> Path:
        meta = SummaryMeta(
            llm_model="fake-ollama",
            prompt_path="prompts/extract_facts.md",
            prompt_hash="fake",
            created_at=f"{day}T09:05:00Z",
        )
        facts = MicroFactsFile(
            facts=[
                MicroFact(
                    id=f"fact-{day}",
                    statement="Capture recorded",
                    confidence=0.5,
                    evidence=FactEvidence(
                        entry_id=f"{day}-entry",
                        spans=[FactEvidenceSpan(type="para", index=0)],
                    ),
                    first_seen=day,
                    last_seen=day,
                )
            ],
        )
        artifact = Artifact[MicroFactsFile](
            kind=ArtifactKind.MICROFACTS_DAILY,
            meta=ArtifactMeta(
                created_at=meta.created_at,
                model=meta.llm_model,
                prompt_path=meta.prompt_path,
                prompt_hash=meta.prompt_hash,
            ),
            data=facts,
        )
        save_artifact(path, artifact)
        return path

    def _write_profile_suggestions_artifact(path: Path, day: str) -> Path:
        meta = SummaryMeta(
            llm_model="fake-ollama",
            prompt_path="prompts/profile_suggest.md",
            prompt_hash="fake",
            created_at=f"{day}T09:10:00Z",
        )
        suggestions = ProfileSuggestions(
            upserts=[
                ProfileSuggestionUpsert(
                    target="claims",
                    operation="upsert",
                    value=make_claim_atom(
                        "pref_capture",
                        "Capture suggestion",
                        strength=0.5,
                        last_updated=f"{day}T09:00:00Z",
                    ),
                )
            ],
            updates=[],
        )
        artifact = Artifact[ProfileSuggestions](
            kind=ArtifactKind.PROFILE_SUGGESTIONS,
            meta=ArtifactMeta(created_at=meta.created_at, model=meta.llm_model),
            data=suggestions,
        )
        save_artifact(path, artifact)
        return path

    def _write_profile_update_batch_artifact(path: Path, day: str) -> Path:
        meta = SummaryMeta(
            llm_model="fake-ollama",
            prompt_path="prompts/characterize.md",
            prompt_hash="fake",
            created_at=f"{day}T09:20:00Z",
        )
        batch = ProfileUpdateBatch(
            batch_id=f"batch-{day}",
            created_at=f"{day}T09:20:00Z",
            date=day,
            inputs=[
                ProfileUpdateInput(
                    id=f"{day}-entry",
                    normalized_path=f"data/normalized/{day}/entry.yaml",
                    tags=["test"],
                )
            ],
            proposals=ProfileUpdateProposals(),
            preview=ProfileUpdatePreview(),
        )
        artifact = Artifact[ProfileUpdateBatch](
            kind=ArtifactKind.PROFILE_UPDATES,
            meta=ArtifactMeta(created_at=meta.created_at, model=meta.llm_model),
            data=batch,
        )
        save_artifact(path, artifact)
        return path

    def fake_run_summarize(date: str, *, timeout: float, retries: int, progress: bool) -> Path:
        del timeout, retries, progress
        stage_calls.append(("summarize", date))
        return _write_summary_artifact(
            tmp_path / "derived" / "summaries" / f"{date}.yaml",
            date,
        )

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
        path = _write_microfacts_artifact(
            tmp_path / "derived" / "microfacts" / f"{date}.yaml",
            date,
        )
        return None, path

    def fake_run_profile_suggest(
        date: str, *, timeout: float, retries: int, progress: bool
    ) -> Path:
        del timeout, retries, progress
        stage_calls.append(("profile_suggest", date))
        return _write_profile_suggestions_artifact(
            tmp_path / "derived" / "profile_suggestions" / f"{date}.yaml",
            date,
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
        return _write_profile_update_batch_artifact(
            tmp_path / "derived" / "pending" / "profile_updates" / f"{date}-batch.yaml",
            date,
        )

    def fake_apply_batch(root: Path, batch_path: Path) -> bool:
        del root
        review_calls.append(batch_path)
        return True

    monkeypatch.setattr("aijournal.commands.summarize.run_summarize", fake_run_summarize)
    monkeypatch.setattr("aijournal.commands.facts.run_facts", fake_run_facts)
    monkeypatch.setattr(
        "aijournal.commands.profile.run_profile_suggest",
        fake_run_profile_suggest,
    )
    monkeypatch.setattr(
        "aijournal.commands.profile.run_profile_apply",
        fake_run_profile_apply,
    )
    monkeypatch.setattr(
        "aijournal.commands.characterize.run_characterize",
        fake_run_characterize,
    )
    dummy_claim = object()
    monkeypatch.setattr(
        "aijournal.commands.profile.load_profile_components",
        lambda root: (None, [dummy_claim]),
    )
    monkeypatch.setattr(
        "aijournal.commands.index.run_index_rebuild",
        lambda since, *, limit: index_rebuild_calls.append((since, limit)) or "rebuild",
    )
    monkeypatch.setattr(
        "aijournal.commands.index.run_index_tail",
        lambda since, *, days, limit: index_tail_calls.append((since, days, limit)) or "updated",
    )
    monkeypatch.setattr(
        "aijournal.services.capture.utils.apply_profile_update_batch",
        fake_apply_batch,
    )

    persona_states = [
        ("stale", ["needs rebuild"]),
        ("fresh", []),
    ]

    def fake_persona_state(root: Path) -> tuple[str, list[str]]:
        persona_state_calls.append(root)
        return persona_states.pop(0) if persona_states else ("fresh", [])

    def fake_run_persona_build(
        profile: dict[str, object],
        claim_models: list[object],
        *,
        config: dict[str, object],
        root: Path | None = None,
    ) -> tuple[Path, bool]:
        del config
        persona_build_calls.append((profile, claim_models))
        persona_path = _ensure_file(
            tmp_path / "derived" / "persona" / "persona_core.yaml",
            "persona",
        )
        return persona_path, True

    monkeypatch.setattr("aijournal.commands.persona.persona_state", fake_persona_state)
    monkeypatch.setattr("aijournal.commands.persona.run_persona_build", fake_run_persona_build)
    monkeypatch.setattr(
        "aijournal.commands.pack.run_pack",
        lambda level, date, *, output, max_tokens, fmt, history_days, dry_run: pack_calls.append(
            (level, output)
        ),
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
        "refresh.index",
        "refresh.persona",
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
    assert index_rebuild_calls == [(None, None)]
    assert not index_tail_calls
    assert len(persona_build_calls) == 1
    assert len(persona_state_calls) == 2
    assert not pack_calls

    manifest_path = tmp_path / "data" / "manifest" / "ingested.yaml"
    assert manifest_path.exists()
    assert result.persona_stale_before is True
    assert result.persona_stale_after is False
    assert result.index_rebuilt is True
    assert not result.warnings
    assert result.review_candidates
    assert result.telemetry_path is not None
    telemetry_file = tmp_path / result.telemetry_path
    assert telemetry_file.exists()
    for candidate in result.review_candidates:
        assert (tmp_path / candidate).exists()
    events = [
        json.loads(line)
        for line in telemetry_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    event_names = {event.get("event") for event in events}
    assert all("timestamp" in event for event in events)
    expected = {
        "preflight",
        "persist",
        "normalize",
        "derive.summarize",
        "derive.extract_facts",
        "derive.profile_suggest",
        "derive.profile_apply",
        "derive.characterize",
        "derive.review",
        "index.rebuild",
        "persona.status",
        "done",
    }
    assert expected.issubset(event_names)


def test_run_capture_rebuild_skip_skips_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")
    monkeypatch.setattr(
        "aijournal.utils.time.now",
        lambda: datetime(2025, 10, 28, 11, 0, tzinfo=UTC),
    )
    monkeypatch.chdir(tmp_path)

    def _ensure_file(path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    monkeypatch.setattr(
        "aijournal.commands.summarize.run_summarize",
        lambda date, *, timeout, retries, progress: _ensure_file(
            tmp_path / "derived" / "summaries" / f"{date}.yaml",
            "summary",
        ),
    )
    monkeypatch.setattr(
        "aijournal.commands.facts.run_facts",
        lambda date, *, timeout, retries, progress, claim_models, build_claim_preview: (
            None,
            _ensure_file(tmp_path / "derived" / "microfacts" / f"{date}.yaml", "facts"),
        ),
    )
    monkeypatch.setattr(
        "aijournal.commands.profile.run_profile_suggest",
        lambda date, *, timeout, retries, progress: _ensure_file(
            tmp_path / "derived" / "profile_suggestions" / f"{date}.yaml",
            "suggest",
        ),
    )
    monkeypatch.setattr(
        "aijournal.commands.profile.run_profile_apply",
        lambda *args, **kwargs: "Applied",
    )
    monkeypatch.setattr(
        "aijournal.commands.characterize.run_characterize",
        lambda date, *, timeout, retries, progress, build_claim_preview: _ensure_file(
            tmp_path / "derived" / "pending" / "profile_updates" / f"{date}-batch.yaml",
            "batch",
        ),
    )
    monkeypatch.setattr(
        "aijournal.services.capture.utils.apply_profile_update_batch",
        lambda root, batch_path: True,
    )

    monkeypatch.setattr(
        "aijournal.services.capture.stages.stage6_index.run_index_stage_6",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("index stage should be skipped when rebuild=skip")
        ),
    )
    monkeypatch.setattr(
        "aijournal.services.capture.stages.stage7_persona.run_persona_stage_7",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("persona stage should be skipped when rebuild=skip")
        ),
    )

    inputs = CaptureInput(
        source="stdin",
        text="skip refresh",
        title="Skip",
        rebuild="skip",
    )
    result = run_capture(inputs)

    assert 6 in result.stages_skipped
    assert 7 in result.stages_skipped
    assert result.index_rebuilt is False
    assert result.durations_ms["refresh.index"] == 0.0
    assert result.durations_ms["refresh.persona"] == 0.0


def test_run_capture_rebuild_always_forces_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")
    monkeypatch.setattr(
        "aijournal.utils.time.now",
        lambda: datetime(2025, 10, 28, 12, 0, tzinfo=UTC),
    )
    monkeypatch.chdir(tmp_path)

    def _ensure_file(path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    monkeypatch.setattr(
        "aijournal.commands.summarize.run_summarize",
        lambda date, *, timeout, retries, progress: _ensure_file(
            tmp_path / "derived" / "summaries" / f"{date}.yaml",
            "summary",
        ),
    )
    monkeypatch.setattr(
        "aijournal.commands.facts.run_facts",
        lambda date, *, timeout, retries, progress, claim_models, build_claim_preview: (
            None,
            _ensure_file(tmp_path / "derived" / "microfacts" / f"{date}.yaml", "facts"),
        ),
    )
    monkeypatch.setattr(
        "aijournal.commands.profile.run_profile_suggest",
        lambda date, *, timeout, retries, progress: _ensure_file(
            tmp_path / "derived" / "profile_suggestions" / f"{date}.yaml",
            "suggest",
        ),
    )
    monkeypatch.setattr(
        "aijournal.commands.profile.run_profile_apply",
        lambda *args, **kwargs: "Applied",
    )
    monkeypatch.setattr(
        "aijournal.commands.characterize.run_characterize",
        lambda date, *, timeout, retries, progress, build_claim_preview: _ensure_file(
            tmp_path / "derived" / "pending" / "profile_updates" / f"{date}-batch.yaml",
            "batch",
        ),
    )
    monkeypatch.setattr(
        "aijournal.services.capture.utils.apply_profile_update_batch",
        lambda root, batch_path: True,
    )

    index_rebuild_calls: list[tuple[str | None, int | None]] = []
    index_tail_calls: list[tuple[str | None, int, int | None]] = []

    def fake_run_index_rebuild(since: str | None, *, limit: int | None) -> str:
        index_rebuild_calls.append((since, limit))
        index_root = tmp_path / "derived" / "index"
        index_root.mkdir(parents=True, exist_ok=True)
        (index_root / "index.db").write_text("db", encoding="utf-8")
        (index_root / "annoy.index").write_text("ann", encoding="utf-8")
        return "rebuild"

    monkeypatch.setattr(
        "aijournal.commands.index.run_index_rebuild",
        fake_run_index_rebuild,
    )
    monkeypatch.setattr(
        "aijournal.commands.index.run_index_tail",
        lambda since, *, days, limit: index_tail_calls.append((since, days, limit)) or "updated",
    )

    persona_states = [
        ("stale", ["needs rebuild"]),
        ("fresh", []),
        ("fresh", []),
        ("fresh", []),
    ]
    persona_build_calls: list[tuple[dict[str, object], list[object]]] = []

    def fake_persona_state(root: Path) -> tuple[str, list[str]]:
        return persona_states.pop(0)

    def fake_run_persona_build(
        profile: dict[str, object],
        claim_models: list[object],
        *,
        config: dict[str, object],
        root: Path | None = None,
    ) -> tuple[Path, bool]:
        persona_build_calls.append((profile, claim_models))
        persona_path = _ensure_file(
            tmp_path / "derived" / "persona" / "persona_core.yaml",
            "persona",
        )
        return persona_path, True

    monkeypatch.setattr("aijournal.commands.persona.persona_state", fake_persona_state)
    monkeypatch.setattr("aijournal.commands.persona.run_persona_build", fake_run_persona_build)
    monkeypatch.setattr(
        "aijournal.commands.profile.load_profile_components",
        lambda root: ({"name": "Test"}, [object()]),
    )
    monkeypatch.setattr(
        "aijournal.commands.profile.profile_to_dict",
        lambda model: model if isinstance(model, dict) else {},
    )

    inputs_first = CaptureInput(
        source="stdin",
        text="force refresh",
        title="Force",
    )
    run_capture(inputs_first)

    inputs_second = CaptureInput(
        source="stdin",
        text="force refresh",
        title="Force",
        rebuild="always",
    )
    result = run_capture(inputs_second)

    # index rebuild should have been triggered twice (initial + forced)
    assert index_rebuild_calls == [(None, None), (None, None)]
    # forced rebuild should not rely on tailing
    assert index_tail_calls == []
    # persona build should run on both captures (stale first, forced second)
    assert len(persona_build_calls) == 2
    assert result.index_rebuilt is True
    assert result.persona_stale_before is False
    assert result.persona_stale_after is False


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
        "aijournal.commands.summarize.run_summarize",
        lambda date, *, timeout, retries, progress: _ensure_file(
            tmp_path / "derived" / "summaries" / f"{date}.yaml", "summary"
        ),
    )

    monkeypatch.setattr(
        "aijournal.commands.facts.run_facts",
        lambda date, *, timeout, retries, progress, claim_models, build_claim_preview: (
            None,
            _ensure_file(tmp_path / "derived" / "microfacts" / f"{date}.yaml", "facts"),
        ),
    )

    monkeypatch.setattr(
        "aijournal.commands.profile.run_profile_suggest",
        lambda date, *, timeout, retries, progress: _ensure_file(
            tmp_path / "derived" / "profile_suggestions" / f"{date}.yaml",
            "suggest",
        ),
    )

    monkeypatch.setattr(
        "aijournal.commands.characterize.run_characterize",
        lambda date, *, timeout, retries, progress, build_claim_preview: _ensure_file(
            tmp_path / "derived" / "pending" / "profile_updates" / f"{date}-batch.yaml",
            "batch",
        ),
    )

    profile_apply_calls: list[str] = []
    review_calls: list[Path] = []

    monkeypatch.setattr(
        "aijournal.commands.profile.run_profile_apply",
        lambda *args, **kwargs: profile_apply_calls.append("called"),
    )

    monkeypatch.setattr(
        "aijournal.commands.profile.load_profile_components",
        lambda root: (None, []),
    )
    index_rebuild_calls: list[tuple[str | None, int | None]] = []
    monkeypatch.setattr(
        "aijournal.commands.index.run_index_rebuild",
        lambda since, *, limit: index_rebuild_calls.append((since, limit)) or "rebuild",
    )
    monkeypatch.setattr(
        "aijournal.commands.index.run_index_tail",
        lambda since, *, days, limit: (_ for _ in ()).throw(AssertionError("tail should not run")),
    )
    persona_states = [
        ("fresh", []),
        ("fresh", []),
    ]
    monkeypatch.setattr(
        "aijournal.commands.persona.persona_state",
        lambda root: persona_states.pop(0),
    )
    monkeypatch.setattr(
        "aijournal.commands.persona.run_persona_build",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("persona build should not run")
        ),
    )
    monkeypatch.setattr(
        "aijournal.commands.pack.run_pack",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pack should not run")),
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
    assert index_rebuild_calls == [(None, None)]
    assert result.index_rebuilt is True
    assert result.persona_stale_before is False
    assert result.persona_stale_after is False


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


def test_persist_file_records_snapshot_and_manifest_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aijournal.utils.time.now",
        lambda: datetime(2025, 10, 28, 7, 30, tzinfo=UTC),
    )
    entry_path = tmp_path / "imports" / "json-entry.md"
    entry_path.parent.mkdir(parents=True, exist_ok=True)
    entry_path.write_text(
        textwrap.dedent(
            """
            {
              "id": "json-entry",
              "created_at": "2025-10-20T05:00:00Z",
              "title": "JSON Import",
              "tags": ["json", "import"],
              "projects": ["capture-phase6"]
            }

            Body from JSON frontmatter.
            """
        ).strip(),
        encoding="utf-8",
    )

    inputs = CaptureInput(
        source="file",
        paths=[str(entry_path)],
        source_type="notes",
        tags=["cli"],
        projects=["proj"],
    )
    manifest: list[ManifestEntry] = []
    result = _persist_file_entry(inputs, tmp_path, manifest, source_path=entry_path, snapshot=True)

    assert result.changed is True
    manifest_entry = manifest[-1]
    assert manifest_entry.snapshot_path is not None
    snapshot_path = tmp_path / manifest_entry.snapshot_path
    assert snapshot_path.exists()
    assert snapshot_path.read_bytes() == entry_path.read_bytes()
    assert manifest_entry.canonical_journal_path == result.markdown_path

    markdown_path = tmp_path / result.markdown_path
    markdown_text = markdown_path.read_text(encoding="utf-8")
    frontmatter_yaml = markdown_text.split("---\n", 1)[1].split("\n---", 1)[0]
    origin_metadata = yaml.safe_load(frontmatter_yaml)
    assert origin_metadata["origin"]["canonical_path"] == result.markdown_path
    assert origin_metadata["origin"]["snapshot_path"] == manifest_entry.snapshot_path


def test_persist_file_slug_collision_logs_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aijournal.utils.time.now",
        lambda: datetime(2025, 10, 28, 9, 0, tzinfo=UTC),
    )
    entry_one = tmp_path / "first.md"
    entry_two = tmp_path / "second.md"
    entry_one.write_text(
        "---\nid: collide\ncreated_at: 2025-10-27\ntitle: First\n---\nBody one",
        encoding="utf-8",
    )
    entry_two.write_text(
        "---\nid: collide\ncreated_at: 2025-10-27\ntitle: Second\n---\nBody two",
        encoding="utf-8",
    )

    manifest: list[ManifestEntry] = []
    inputs = CaptureInput(source="file", paths=[str(entry_one)])
    _persist_file_entry(inputs, tmp_path, manifest, source_path=entry_one, snapshot=False)

    inputs_two = CaptureInput(source="file", paths=[str(entry_two)])
    result_two = _persist_file_entry(
        inputs_two, tmp_path, manifest, source_path=entry_two, snapshot=False
    )

    assert result_two.slug.endswith("-2")
    assert result_two.warnings
    assert "stored as" in result_two.warnings[0]
    manifest_entry = manifest[-1]
    assert manifest_entry.aliases == ["collide"]
    assert manifest_entry.snapshot_path is None


def test_discover_markdown_files_recurses(tmp_path: Path) -> None:
    (tmp_path / "nested" / "inner").mkdir(parents=True, exist_ok=True)
    file_one = tmp_path / "root.md"
    file_two = tmp_path / "nested" / "note.markdown"
    file_three = tmp_path / "nested" / "inner" / "journal.md"
    for path in (file_one, file_two, file_three):
        path.write_text("---\nid: test\ncreated_at: 2025-10-27\n---\nBody", encoding="utf-8")
    # Non-markdown file should be ignored.
    (tmp_path / "nested" / "ignore.txt").write_text("ignore", encoding="utf-8")

    discovered = discover_markdown_files([str(tmp_path)])
    relative = [path.relative_to(tmp_path) for path in discovered]
    assert relative == [
        Path("nested/inner/journal.md"),
        Path("nested/note.markdown"),
        Path("root.md"),
    ]
