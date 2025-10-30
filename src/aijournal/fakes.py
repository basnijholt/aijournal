"""Deterministic fake generators used when running in offline mode."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from aijournal.domain.changes import (
    ClaimAtomInput,
    ClaimProposal,
    FacetChange,
    ProfileUpdateProposals,
)
from aijournal.domain.evidence import SourceRef
from aijournal.domain.facts import (
    DailySummary,
    FactEvidence,
    MicroFact,
)
from aijournal.domain.journal import NormalizedEntry
from aijournal.models.claim_atoms import ClaimAtom
from aijournal.models.derived import (
    AdviceCard,
    AdviceRecommendation,
    AdviceReference,
    ProfileSuggestions,
    ProfileSuggestionUpdate,
    ProfileSuggestionUpsert,
)
from aijournal.utils import time as time_utils


def fake_summarize(
    entries: Iterable[NormalizedEntry],
    date: str,
    *,
    todo_builder: Callable[[Sequence[NormalizedEntry]], list[str]],
) -> DailySummary:
    entry_list = list(entries)
    bullets: list[str] = []
    for entry in entry_list:
        title = entry.title or entry.id
        sections = entry.sections or []
        section_titles = ", ".join(section.heading for section in sections[:2] if section.heading)
        if section_titles:
            bullets.append(f"{title}: {section_titles}")
        else:
            bullets.append(f"{title}: no sections")
    if not bullets:
        bullets = ["No content available"]
    return DailySummary(
        day=date,
        bullets=bullets,
        highlights=bullets[:3],
        todo_candidates=todo_builder(entry_list),
    )


def fake_microfacts(entries: Iterable[NormalizedEntry]) -> list[MicroFact]:
    facts: list[MicroFact] = []
    for idx, entry in enumerate(entries, start=1):
        entry_id = str(entry.id or f"entry-{idx}")
        title = entry.title or entry_id
        sections = entry.sections or []
        statement = f"{title} covers {len(sections)} sections"
        facts.append(
            MicroFact(
                id=f"fact-{entry_id}",
                statement=statement,
                confidence=0.8,
                evidence=FactEvidence(entry_id=entry_id),
            ),
        )

    if facts:
        return facts

    return [
        MicroFact(
            id="fact-empty",
            statement="No normalized entries available",
            confidence=0.0,
            evidence=FactEvidence(entry_id="unknown"),
        ),
    ]


def fake_advise(
    question: str,
    profile: dict[str, Any],
    claims: Sequence[ClaimAtom],
    *,
    advice_identifier: Callable[[str], str],
    rankings: Sequence[Any] | None = None,
    pending_prompts: Sequence[str] | None = None,
) -> AdviceCard:
    primary_claim = claims[0] if claims else None
    advice_id = advice_identifier(question)
    claim_statement = primary_claim.statement if primary_claim else "Reflect on priorities"
    claim_id = primary_claim.id if primary_claim else None

    facets: list[str] = []
    if profile.get("affect_energy"):
        facets.append("affect_energy.energy_map")
    if profile.get("goals"):
        facets.append("goals.short_term")
    if profile.get("values_motivations"):
        facets.append("values_motivations.schwartz_top5")

    alignment = AdviceReference(facets=facets, claims=[claim_id] if claim_id else [])

    top_priority = rankings[0] if rankings else None
    assumption_lines = []
    if claim_statement:
        assumption_lines.append(f"Reference claim: {claim_statement}")
    if top_priority:
        kind = getattr(top_priority, "kind", None)
        if kind == "claim" and getattr(top_priority, "claim_id", None):
            alignment.claims = list({top_priority.claim_id, *alignment.claims})
        elif kind == "facet" and getattr(top_priority, "path", None):
            alignment.facets = list({top_priority.path, *alignment.facets})

    recommendation = AdviceRecommendation(
        title=claim_statement,
        why_this_fits_you=AdviceReference(
            facets=list(alignment.facets),
            claims=list(alignment.claims),
        ),
        steps=[
            "Protect two deep-work mornings for focused execution.",
            f"Question under review: {question}",
        ],
        risks=["Schedule collisions", "Unclear stakeholder updates"],
        mitigations=[
            "Share the plan with collaborators early.",
            "Add end-of-day shutdown reminders to honor boundaries.",
        ],
    )

    if pending_prompts:
        recommendation.steps.append(f"Journal on pending prompt: {pending_prompts[0]}")

    style = profile.get("coaching_prefs") or {"tone": "direct", "depth": "concrete-first"}

    return AdviceCard(
        id=advice_id,
        query=question,
        assumptions=assumption_lines or ["No verified claims available"],
        recommendations=[recommendation],
        tradeoffs=["Shipping speed may dip slightly while routines stabilize."],
        next_actions=[
            "Block two 3-hour focus windows next week.",
            "Schedule a 10-minute Friday review with yourself.",
        ],
        confidence=0.5,
        alignment=alignment,
        style=style,
    )


def fake_profile_suggestions(
    entries: Sequence[NormalizedEntry],
    profile: dict[str, Any],
    claims: Sequence[ClaimAtom],
    *,
    build_claim: Callable[..., ClaimAtom],
) -> ProfileSuggestions:
    upserts: list[ProfileSuggestionUpsert] = []
    updates: list[ProfileSuggestionUpdate] = []

    for entry in entries[:1]:
        statement = entry.title or "New observation"
        claim_id = f"auto_{entry.id or 'entry'}"
        claim_model = build_claim(
            entry,
            claim_id=claim_id,
            statement=statement,
            strength=0.6,
            status="tentative",
        )
        upserts.append(
            ProfileSuggestionUpsert(
                target="claims",
                operation="upsert",
                value=claim_model.model_copy(deep=True),
            ),
        )

    if profile:
        updates.append(
            ProfileSuggestionUpdate(
                target="values_motivations.schwartz_top5",
                operation="update",
                value=profile.get("values_motivations", {}).get("schwartz_top5", []),
            ),
        )

    return ProfileSuggestions(upserts=upserts, updates=updates)


def fake_characterize(
    entries: Sequence[NormalizedEntry],
    profile: dict[str, Any],
    claims: Sequence[ClaimAtom],
    *,
    build_claim: Callable[..., ClaimAtom],
) -> ProfileUpdateProposals:
    if not entries:
        return ProfileUpdateProposals()

    seed = entries[0]
    date = time_utils.created_date(seed.created_at or time_utils.format_timestamp(time_utils.now()))
    heading = ""
    sections = seed.sections or []
    if sections:
        heading = sections[0].heading or ""
    title = seed.title or seed.id or "entry"
    theme = heading or title
    tag = (seed.tags or [theme])[0]
    claim_id = f"{time_utils.slugify_title(theme) or 'entry'}-{date.replace('-', '')}-claim"
    claim = build_claim(
        seed,
        claim_id=claim_id[:64],
        statement=f"{theme} remains top-of-mind on {date}.",
        strength=0.64,
        status="tentative",
    )

    facet = FacetChange(
        path="values_motivations.recurring_theme",
        value={
            "label": theme,
            "tag_hint": tag,
            "last_seen": date,
        },
        operation="set",
        method="inferred",
        confidence=0.55,
        review_after_days=90,
        user_verified=False,
    )

    claim_input = _claim_atom_to_input(claim)
    evidence = [
        SourceRef.model_validate(source.model_dump(mode="python"))
        for source in claim.provenance.sources
    ]

    claim_proposal = ClaimProposal(
        claim=claim_input,
        normalized_ids=[],
        evidence=evidence,
        manifest_hashes=[],
        rationale=None,
    )

    return ProfileUpdateProposals(
        claims=[claim_proposal],
        facets=[facet],
    )


def _claim_atom_to_input(claim: ClaimAtom) -> ClaimAtomInput:
    return ClaimAtomInput(
        type=claim.type,
        subject=claim.subject,
        predicate=claim.predicate,
        value=claim.value,
        statement=claim.statement,
        scope=claim.scope.model_copy(deep=True),
        strength=claim.strength,
        status=claim.status,
        method=claim.method,
        user_verified=claim.user_verified,
        review_after_days=claim.review_after_days,
    )
