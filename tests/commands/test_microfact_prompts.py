"""Tests ensuring consolidated microfacts flow into downstream prompts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aijournal.commands import characterize, profile
from aijournal.common.app_config import AppConfig
from aijournal.domain.facts import (
    ConsolidatedMicroFact,
    ConsolidatedMicrofactsFile,
    DailySummary,
)
from aijournal.domain.journal import NormalizedEntry
from aijournal.domain.prompts import PromptProfileUpdates
from aijournal.models.authoritative import ManifestEntry


def _sample_consolidated() -> ConsolidatedMicrofactsFile:
    return ConsolidatedMicrofactsFile(
        generated_at="2025-01-05T00:00:00Z",
        embedding_model="fake-model",
        facts=[
            ConsolidatedMicroFact(
                id="recurring.focus",
                statement="Blocks 8-10am for deep work",
                canonical_statement="blocks 8-10am for deep work",
                confidence=0.82,
                first_seen="2025-01-01",
                last_seen="2025-01-05",
                observation_count=3,
                domain="journal",
                contexts=["focus"],
                evidence_entries=["entry-1", "entry-2"],
                source_fact_ids=["2025-01-01:focus"],
            )
        ],
    )


def _sample_entry() -> NormalizedEntry:
    return NormalizedEntry(
        id="entry-1",
        created_at="2025-01-05T08:00:00Z",
        source_path="data/journal/entry.md",
        title="Focus Log",
    )


def _sample_summary(day: str = "2025-01-05") -> DailySummary:
    return DailySummary(
        day=day,
        bullets=["Tracked focus rituals"],
        highlights=["Early deep work block"],
        todo_candidates=["Refine planning block"],
    )


def test_profile_payload_includes_consolidated_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, str] = {}

    monkeypatch.setattr(profile, "use_fake_llm", lambda: False)
    monkeypatch.setattr(
        profile,
        "load_consolidated_microfacts",
        lambda workspace, config: _sample_consolidated(),
    )

    def fake_invoke(
        prompt_path: str, variables: dict[str, str], **_: object
    ) -> PromptProfileUpdates:
        captured.update(variables)
        return PromptProfileUpdates()

    monkeypatch.setattr(profile, "_invoke_structured_llm", fake_invoke)

    profile._profile_proposals_payload(
        [_sample_entry()],
        summary=_sample_summary(),
        profile={},
        claims=[],
        date="2025-01-05",
        config=AppConfig(),
        workspace=tmp_path,
        timeout=5.0,
    )

    consolidated_payload = json.loads(captured["consolidated_facts_json"])
    assert consolidated_payload["facts"][0]["observation_count"] == 3


def test_characterize_payload_includes_consolidated_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, str] = {}

    monkeypatch.setattr(characterize, "use_fake_llm", lambda: False)
    monkeypatch.setattr(
        characterize,
        "load_consolidated_microfacts",
        lambda workspace, config: _sample_consolidated(),
    )

    def fake_invoke(
        prompt_path: str, variables: dict[str, str], **_: object
    ) -> PromptProfileUpdates:
        captured.update(variables)
        return PromptProfileUpdates()

    def fake_structured_call(func, *, retries: int, label: str):  # noqa: ANN001
        return func()

    monkeypatch.setattr(characterize, "_invoke_structured_llm", fake_invoke)

    characterize._characterize_payload(
        date="2025-01-05",
        entries=[_sample_entry()],
        profile={},
        claims=[],
        manifest_index={
            "entry-1": ManifestEntry(
                hash="hash",
                path="path.md",
                normalized="normalized.yaml",
                source_type="journal",
                ingested_at="2025-01-05T08:00:00Z",
                created_at="2025-01-05T08:00:00Z",
                id="entry-1",
            )
        },
        config=AppConfig(),
        workspace=tmp_path,
        timeout=5.0,
        retries=1,
        use_fake_llm=False,
        normalize_claims=lambda *args, **kwargs: [],
        invoke_structured_llm=fake_invoke,
        structured_call=fake_structured_call,
        summary=_sample_summary(),
        summary_window=[("2025-01-04", _sample_summary("2025-01-04"))],
    )

    consolidated_payload = json.loads(captured["consolidated_facts_json"])
    assert consolidated_payload["facts"][0]["statement"].startswith("Blocks 8-10am")
