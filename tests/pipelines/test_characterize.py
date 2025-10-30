from __future__ import annotations

from collections.abc import Callable

from aijournal.domain.changes import ClaimAtomInput
from aijournal.models import (
    ClaimAtom,
    ClaimProposal,
    ClaimSource,
    ClaimSourceSpan,
    FacetChange,
    NormalizedEntry,
    ProfileUpdateProposals,
    Provenance,
    Scope,
)
from aijournal.pipelines import characterize


def _normalized_entry(entry_id: str) -> NormalizedEntry:
    return NormalizedEntry(
        id=entry_id,
        created_at="2024-01-02T09:00:00Z",
        source_path=f"data/{entry_id}.md",
        title="Test Entry",
        tags=["focus"],
        sections=[],
        source_hash="hash-1",
    )


def _claim(entry_id: str) -> ClaimAtom:
    return ClaimAtom(
        id=f"{entry_id}-claim",
        type="preference",
        subject="Subject",
        predicate="predicate",
        value="Value",
        statement="Statement",
        scope=Scope(),
        strength=0.6,
        status="tentative",
        method="inferred",
        user_verified=False,
        review_after_days=120,
        provenance=Provenance(
            sources=[ClaimSource(entry_id=entry_id, spans=[])],
            first_seen="2024-01-01",
            last_updated="2024-01-02T00:00:00Z",
            observation_count=1,
        ),
    )


def _context(entry_id: str) -> tuple[list[str], list[str], list[ClaimSource]]:
    return (
        [entry_id],
        ["manifest-1"],
        [
            ClaimSource(
                entry_id=entry_id,
                spans=[ClaimSourceSpan(type="excerpt", index=0, start=None, end=None)],
            )
        ],
    )


def _claim_input(entry_id: str) -> ClaimAtomInput:
    base = _claim(entry_id)
    return ClaimAtomInput(
        type=base.type,
        subject=base.subject,
        predicate=base.predicate,
        value=base.value,
        statement=base.statement,
        scope=base.scope.model_copy(deep=True),
        strength=base.strength,
        status=base.status,
        method=base.method,
        user_verified=base.user_verified,
        review_after_days=base.review_after_days,
    )


def test_generate_characterization_fake_mode(monkeypatch) -> None:
    entry = _normalized_entry("entry-1")
    profile = {"traits": {"strengths": ["Focus"]}}
    claims = [_claim("entry-1")]
    context = _context("entry-1")

    def request_factory() -> ProfileUpdateProposals:  # pragma: no cover - fake path skips
        raise AssertionError("Structured request should not run in fake mode")

    def structured_call(  # pragma: no cover - fake path skips
        func: Callable[[], ProfileUpdateProposals],
        *,
        retries: int,
        label: str,
    ) -> ProfileUpdateProposals:
        raise AssertionError("structured_call invoked in fake mode")

    captured: dict[str, object] = {}

    def normalize_claims(raw_claims, **kwargs):
        captured["raw_claims"] = raw_claims
        return [
            ClaimProposal(
                claim=_claim_input("entry-1"),
                normalized_ids=[],
                evidence=[],
                manifest_hashes=[],
            )
        ]

    def normalize_facets(raw_facets, **kwargs):
        captured["raw_facets"] = raw_facets
        return []

    result, prompts = characterize.generate_characterization(
        [entry],
        profile,
        claims,
        use_fake_llm=True,
        structured_call=structured_call,
        request_factory=request_factory,
        retries=2,
        label="characterize 2024-01-02",
        context=context,
        claim_timestamp="2024-01-02T09:00:00Z",
        build_claim=lambda *_args, **_kwargs: _claim("entry-1"),
        normalize_claims=normalize_claims,
        normalize_facets=normalize_facets,
    )

    assert isinstance(result, ProfileUpdateProposals)
    assert "raw_claims" in captured
    assert prompts == []


def test_generate_characterization_normalizes_llm_payload(monkeypatch) -> None:
    entry = _normalized_entry("entry-1")
    profile = {}
    claims: list[ClaimAtom] = []
    context = _context("entry-1")

    response = ProfileUpdateProposals(
        claims=[
            {
                "claim": {
                    "type": "preference",
                    "subject": "Focus routines",
                    "predicate": "affinity",
                    "value": "Focus improved",
                    "statement": "Focus improved",
                    "scope": {"domain": None, "context": [], "conditions": []},
                    "strength": 0.6,
                    "status": "tentative",
                    "method": "inferred",
                    "user_verified": False,
                    "review_after_days": 120,
                },
                "normalized_ids": ["entry-1"],
                "manifest_hashes": ["manifest-1"],
                "rationale": "Recent entry reinforces the pattern.",
            }
        ],
        facets=[
            {
                "path": "values_motivations.primary_focus",
                "operation": "set",
                "value": "Deep Work",
                "confidence": 0.7,
            }
        ],
        interview_prompts=["What helped you focus this week?"],
    )

    def request_factory() -> ProfileUpdateProposals:
        return response

    call_args: dict[str, object] = {}

    def structured_call(
        func: Callable[[], ProfileUpdateProposals],
        *,
        retries: int,
        label: str,
    ) -> ProfileUpdateProposals:
        call_args["retries"] = retries
        call_args["label"] = label
        return func()

    captured: dict[str, object] = {}

    def normalize_claims(raw_claims, **kwargs):
        captured["claims"] = raw_claims
        return [
            ClaimProposal(
                claim=_claim_input("entry-1"),
                normalized_ids=[],
                evidence=[ClaimSource(entry_id="entry-1", spans=[])],
                manifest_hashes=[],
            )
        ]

    def normalize_facets(raw_facets, **kwargs):
        captured["facets"] = raw_facets
        return [
            FacetChange(
                path="values_motivations.primary_focus",
                value="Deep Work",
                operation="set",
                method="inferred",
            )
        ]

    result, prompts = characterize.generate_characterization(
        [entry],
        profile,
        claims,
        use_fake_llm=False,
        structured_call=structured_call,
        request_factory=request_factory,
        retries=3,
        label="characterize 2024-01-02",
        context=context,
        claim_timestamp="2024-01-02T12:00:00Z",
        build_claim=lambda *_args, **_kwargs: _claim("entry-1"),  # pragma: no cover
        normalize_claims=normalize_claims,
        normalize_facets=normalize_facets,
    )

    assert call_args == {"retries": 3, "label": "characterize 2024-01-02"}
    assert "claims" in captured and "facets" in captured
    assert isinstance(result, ProfileUpdateProposals)
    assert prompts == ["What helped you focus this week?"]
