"""Tests ensuring consolidated microfacts flow into profile update prompts."""

from __future__ import annotations

import json
from pathlib import Path

from aijournal.commands import profile_update as profile_update_module
from aijournal.common.app_config import AppConfig
from aijournal.domain.facts import ConsolidatedMicroFact, ConsolidatedMicrofactsFile


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
            ),
        ],
    )


def test_profile_update_consolidated_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        profile_update_module,
        "load_consolidated_microfacts",
        lambda workspace, config: _sample_consolidated(),
    )
    monkeypatch.setattr(
        profile_update_module,
        "select_recurring_facts",
        lambda snapshot, **_: [
            {
                "statement": fact.statement,
                "observation_count": fact.observation_count,
                "first_seen": fact.first_seen,
                "last_seen": fact.last_seen,
                "contexts": fact.contexts,
                "evidence_entries": fact.evidence_entries,
            }
            for fact in snapshot.facts
        ],
    )

    payload = profile_update_module._load_consolidated_facts_json(Path("/tmp"), AppConfig())
    consolidated_payload = json.loads(payload)
    assert consolidated_payload["facts"][0]["observation_count"] == 3


def test_profile_update_consolidated_payload_missing_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        profile_update_module,
        "load_consolidated_microfacts",
        lambda workspace, config: None,
    )

    payload = profile_update_module._load_consolidated_facts_json(Path("/tmp"), AppConfig())
    assert payload == "{}"
