from __future__ import annotations

from collections.abc import Callable

from aijournal.models import (
    CharacterizeResponse,
    ClaimAtom,
    ClaimProposal,
    ClaimSource,
    ClaimSourceSpan,
    FacetProposal,
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


def _context(entry_id: str) -> tuple[list[str], list[str], list[str], list[ClaimSource]]:
    return (
        [entry_id],
        ["hash-1"],
        ["manifest-1"],
        [
            ClaimSource(
                entry_id=entry_id,
                spans=[ClaimSourceSpan(type="excerpt", index=0, start=None, end=None)],
            )
        ],
    )


def test_generate_characterization_fake_mode(monkeypatch) -> None:
    entry = _normalized_entry("entry-1")
    profile = {"traits": {"strengths": ["Focus"]}}
    claims = [_claim("entry-1")]
    context = _context("entry-1")

    def request_factory() -> CharacterizeResponse:  # pragma: no cover - fake path skips
        raise AssertionError("Structured request should not run in fake mode")

    def structured_call(  # pragma: no cover - fake path skips
        func: Callable[[], CharacterizeResponse],
        *,
        retries: int,
        label: str,
    ) -> CharacterizeResponse:
        raise AssertionError("structured_call invoked in fake mode")

    captured: dict[str, object] = {}

    def normalize_claims(raw_claims, **kwargs):
        captured["raw_claims"] = raw_claims
        return [
            ClaimProposal(
                claim=_claim("entry-1"),
                normalized_ids=[],
                evidence_hashes=[],
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

    response = CharacterizeResponse(
        claims=[
            {
                "claim": {
                    "id": "claim-1",
                    "statement": "Focus improved",
                    "value": "Focus improved",
                },
                "normalized_ids": ["entry-1"],
                "evidence_hashes": ["hash-1"],
                "manifest_hashes": ["manifest-1"],
            }
        ],
        facets=[
            {
                "path": "values_motivations.primary_focus",
                "value": "Deep Work",
                "confidence": 0.7,
            }
        ],
        interview_prompts=["What helped you focus this week?"],
    )

    def request_factory() -> CharacterizeResponse:
        return response

    call_args: dict[str, object] = {}

    def structured_call(
        func: Callable[[], CharacterizeResponse],
        *,
        retries: int,
        label: str,
    ) -> CharacterizeResponse:
        call_args["retries"] = retries
        call_args["label"] = label
        return func()

    captured: dict[str, object] = {}

    def normalize_claims(raw_claims, **kwargs):
        captured["claims"] = raw_claims
        return [
            ClaimProposal(
                claim=_claim("entry-1"), normalized_ids=[], evidence_hashes=[], manifest_hashes=[]
            )
        ]

    def normalize_facets(raw_facets, **kwargs):
        captured["facets"] = raw_facets
        return [
            FacetProposal(
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
