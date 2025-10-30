"""Tests for Pydantic models and YAML helpers (PLAN §3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.io import load_yaml_model, write_yaml_model
from aijournal.io.artifacts import load_artifact, save_artifact
from aijournal.models import (
    AdviceCard,
    AdviceRecommendation,
    AdviceReference,
    Claim,
    ClaimsFile,
    ClaimSource,
    ClaimSourceSpan,
    DailySummary,
    FactEvidence,
    FactEvidenceSpan,
    InterviewQuestion,
    InterviewSet,
    JournalEntry,
    JournalSection,
    MicroFact,
    MicroFactsFile,
    NormalizedEntry,
    PersonaCore,
    PersonaCoreFile,
    PersonaCoreMeta,
    ProfileSuggestions,
    ProfileSuggestionUpdate,
    ProfileSuggestionUpsert,
    Scope,
    SelfProfile,
    SummaryMeta,
)
from aijournal.schema import validate_schema

if TYPE_CHECKING:
    from pathlib import Path


def _fixture_path(tmp_path: Path, name: str) -> Path:
    return tmp_path / f"{name}.yaml"


def _assert_schema(path: Path, schema: str) -> None:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "data" in payload and "kind" in payload:
        validate_schema(schema, payload["data"])
    else:
        validate_schema(schema, payload)


def test_daily_summary_roundtrip(tmp_path: Path) -> None:
    path = _fixture_path(tmp_path, "summary")
    meta = SummaryMeta(
        llm_model="llama3.1:8b-instruct",
        prompt_path="prompts/summarize_day.md",
        prompt_hash="abc123",
        created_at="2025-10-25T12:00:00Z",
    )
    summary = DailySummary(
        day="2025-10-25",
        bullets=["Planned the week"],
        highlights=["Family scheduling sorted"],
        todo_candidates=["Block deep-work mornings"],
        meta=meta,
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
    loaded = load_artifact(path, DailySummary)
    assert loaded.data == summary
    assert loaded.kind is ArtifactKind.SUMMARY_DAILY
    _assert_schema(path, "summary")


def test_claim_file_roundtrip(tmp_path: Path) -> None:
    path = _fixture_path(tmp_path, "claims")
    claim = Claim(
        id="pref.deep_work.window",
        type="preference",
        subject="deep_work",
        predicate="best_window",
        value="09:00-12:00",
        statement="Best deep work between 09:00–12:00 on weekdays.",
        scope=Scope(domain="work", context=["weekday"], conditions=[]),
        strength=0.78,
        status="accepted",
        method="inferred",
        user_verified=True,
        review_after_days=120,
        provenance={
            "sources": [
                ClaimSource(
                    entry_id="2025-10-25_x9t3",
                    spans=[ClaimSourceSpan(type="para", index=0)],
                ),
            ],
            "first_seen": "2024-11-02",
            "last_updated": "2025-10-25T10:10:00Z",
        },
    )
    file = ClaimsFile(claims=[claim])
    write_yaml_model(path, file)

    loaded = load_yaml_model(path, ClaimsFile)
    assert loaded == file
    _assert_schema(path, "claims")


def test_advice_card_roundtrip(tmp_path: Path) -> None:
    path = _fixture_path(tmp_path, "advice")
    reference = AdviceReference(
        facets=["affect_energy.energy_map"],
        claims=["pref_deep_work_morning"],
    )
    recommendation = AdviceRecommendation(
        title="Block two deep-work mornings",
        why_this_fits_you=reference,
        steps=["Hold 09:00–12:00 Mon/Wed", "Route admin to afternoons"],
        risks=["Unexpected pings"],
        mitigations=["Set Slack status"],
    )
    meta = SummaryMeta(
        llm_model="llama3.1:8b-instruct",
        prompt_path="prompts/advise.md",
        prompt_hash="xyz",
        created_at="2025-10-25T12:00:00Z",
    )
    card = AdviceCard(
        id="adv_2025-10-25_01",
        query="How should I schedule my week?",
        assumptions=["You prefer deep work 09:00–12:00"],
        recommendations=[recommendation],
        tradeoffs=["Shipping speed might dip"],
        next_actions=["Add calendar blocks"],
        confidence=0.72,
        alignment=reference,
        style={"tone": "direct"},
        meta=meta,
    )
    write_yaml_model(path, card)

    loaded = load_yaml_model(path, AdviceCard)
    assert loaded == card
    _assert_schema(path, "advice")


def test_journal_and_normalized_models_structure(tmp_path: Path) -> None:
    path = _fixture_path(tmp_path, "normalized")
    entry = NormalizedEntry(
        id="2025-10-25_morning-notes",
        created_at="2025-10-25T09:41:00Z",
        source_path="data/journal/2025/10/25/morning-notes.md",
        title="Morning notes",
        tags=["family"],
        sections=[JournalSection(heading="Had a quiet morning", level=1, para_index=0)],
        summary="Calm start",
        source_hash="abc",
        source_type="journal",
    )
    write_yaml_model(path, entry)
    loaded = load_yaml_model(path, NormalizedEntry)
    assert loaded == entry


def test_persona_core_roundtrip(tmp_path: Path) -> None:
    path = _fixture_path(tmp_path, "persona_core")
    claim = Claim(
        id="pref.test",
        type="preference",
        subject="focus",
        predicate="best_window",
        value="08:00-11:00",
        statement="Best focus early in the day.",
        scope=Scope(domain="work"),
        strength=0.8,
        status="accepted",
        method="inferred",
        user_verified=False,
        review_after_days=120,
        provenance={
            "sources": [
                ClaimSource(entry_id="entry-1", spans=[ClaimSourceSpan(type="para", index=0)]),
            ],
            "first_seen": "2025-01-01",
            "last_updated": "2025-02-01T10:00:00Z",
        },
    )
    persona = PersonaCore(
        profile={"values_motivations": {"drivers": ["Mastery"]}},
        claims=[claim],
    )
    meta = PersonaCoreMeta(
        generated_at="2025-10-25T12:00:00Z",
        token_budget=1200,
        planned_tokens=420,
        char_per_token=4.2,
        selection_strategy="strength*impact*decay",
        claim_pool=1,
        claim_count=1,
    )
    payload = PersonaCoreFile(persona=persona, meta=meta)
    artifact = Artifact[PersonaCoreFile](
        kind=ArtifactKind.PERSONA_CORE,
        meta=ArtifactMeta(
            created_at=meta.generated_at,
            model=None,
        ),
        data=payload,
    )
    save_artifact(path, artifact)

    loaded = load_artifact(path, PersonaCoreFile)
    assert loaded.data == payload
    _assert_schema(path, "persona_core")


def test_microfacts_file_roundtrip(tmp_path: Path) -> None:
    path = _fixture_path(tmp_path, "microfacts")
    meta = SummaryMeta(
        llm_model="llama3.1:8b-instruct",
        prompt_path="prompts/extract_facts.md",
        prompt_hash="def",
        created_at="2025-10-25T12:05:00Z",
    )
    facts = MicroFactsFile(
        facts=[
            MicroFact(
                id="deep_work_morning",
                statement="Morning is best for deep work",
                confidence=0.72,
                evidence=FactEvidence(
                    entry_id="2025-10-25_x9t3",
                    spans=[FactEvidenceSpan(type="para", index=0)],
                ),
                first_seen="2025-10-25",
                last_seen="2025-10-25",
            ),
        ],
        meta=meta,
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
    loaded = load_artifact(path, MicroFactsFile)
    assert loaded.data == facts
    assert loaded.kind is ArtifactKind.MICROFACTS_DAILY
    _assert_schema(path, "microfacts")


def test_load_with_default(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    default = DailySummary(
        day="2025-10-25",
        bullets=[],
        meta=SummaryMeta(
            llm_model="llama3.1:8b-instruct",
            prompt_path="prompts/summarize_day.md",
            prompt_hash=None,
            created_at="2025-10-25T00:00:00Z",
        ),
    )
    loaded = load_yaml_model(missing, DailySummary, default=default)
    assert loaded == default


def test_interview_set_roundtrip(tmp_path: Path) -> None:
    path = _fixture_path(tmp_path, "interviews")
    meta = SummaryMeta(
        llm_model="llama3.1:8b-instruct",
        prompt_path="prompts/profile_probe.md",
        prompt_hash="ghi",
        created_at="2025-10-25T12:10:00Z",
    )
    interviews = InterviewSet(
        questions=[
            InterviewQuestion(
                id="q_values_rank",
                text="Top 3 values you refuse to trade off—rank them.",
                target_facet="values_motivations.schwartz_top5",
                priority="high",
            ),
        ],
        meta=meta,
    )
    write_yaml_model(path, interviews)
    loaded = load_yaml_model(path, InterviewSet)
    assert loaded == interviews
    _assert_schema(path, "interviews")


def test_journal_entry_serialization(tmp_path: Path) -> None:
    path = _fixture_path(tmp_path, "journal")
    entry = JournalEntry(
        id="2025-10-25_x9t3",
        created_at="2025-10-25T09:41:00Z",
        title="Morning notes",
        tags=["family", "planning"],
        mood="calm",
        projects=["aijournal"],
        summary="Planned the week",
    )
    write_yaml_model(path, entry)
    loaded = load_yaml_model(path, JournalEntry)
    assert loaded == entry
    _assert_schema(path, "journal_entry")


def test_profile_suggestions_schema(tmp_path: Path) -> None:
    path = _fixture_path(tmp_path, "profile_suggestions")
    meta = SummaryMeta(
        llm_model="llama3.1:8b-instruct",
        prompt_path="prompts/profile_suggest.md",
        prompt_hash="meta",
        created_at="2025-10-25T12:15:00Z",
    )
    suggestions = ProfileSuggestions(
        upserts=[
            ProfileSuggestionUpsert(
                target="claims",
                operation="upsert",
                value={"id": "pref_focus", "statement": "Focus best before lunch"},
                rationale="Repeated pattern",
            ),
        ],
        updates=[
            ProfileSuggestionUpdate(
                target="values_motivations.schwartz_top5",
                operation="set",
                value=["Self-Direction", "Security"],
                method="inferred",
            ),
        ],
        meta=meta,
    )
    artifact = Artifact[ProfileSuggestions](
        kind=ArtifactKind.PROFILE_SUGGESTIONS,
        meta=ArtifactMeta(created_at=meta.created_at, model=meta.llm_model),
        data=suggestions,
    )
    save_artifact(path, artifact)
    _assert_schema(path, "profile_suggestions")


def test_self_profile_schema(tmp_path: Path) -> None:
    path = _fixture_path(tmp_path, "self_profile")
    profile = SelfProfile(
        traits={"big_five": {"openness": {"score": 0.74, "method": "self_report"}}},
        coaching_prefs={"tone": "direct", "depth": "concrete first"},
        boundaries_ethics={"red_lines": ["No health advice"]},
    )
    write_yaml_model(path, profile)
    _assert_schema(path, "self_profile")
