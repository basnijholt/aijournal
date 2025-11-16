"""Unit tests for the persona export service."""

from __future__ import annotations

import pytest

from aijournal.common.app_config import AppConfig
from aijournal.domain.claims import ClaimAtom
from aijournal.domain.persona import PersonaCore
from aijournal.services.persona_export import (
    PersonaContentError,
    PersonaExportOptions,
    PersonaVariant,
    export_persona_markdown,
)
from tests.helpers import make_claim_atom


def _sample_persona() -> PersonaCore:
    profile = {
        "traits": {
            "identity": {
                "summary": "Local-first systems builder focusing on reproducible tooling.",
                "highlights": ["Ships automation so future-me has leverage."],
            },
        },
        "values_motivations": {
            "core_values": [
                {"value": "Craftsmanship", "why": "Quality over shortcuts."},
                {"value": "Privacy", "why": "Keep data local."},
            ],
        },
        "boundaries_ethics": {
            "guardrails": ["Never leak family data."],
        },
        "coaching_prefs": {
            "tone": "direct",
            "prompts": ["Flag trade-offs", "Ask clarifying questions"],
        },
        "goals": {
            "current_focus": [
                {
                    "value": "Refresh persona pipeline",
                    "timeline": "2025-11",
                },
            ],
        },
        "decision_style": {
            "principles": ["Default to deterministic tooling", "Document changes"],
        },
        "habits": {
            "rituals": ["Weekly review", "Morning focus playlist"],
        },
    }

    claims = [
        ClaimAtom.model_validate(
            make_claim_atom(
                "claim.focus",
                "Mornings are best for deep work",
                subject="focus",
                predicate="window",
                strength=0.82,
                last_updated="2025-02-01T09:00:00Z",
            ),
        ),
        ClaimAtom.model_validate(
            make_claim_atom(
                "claim.async",
                "Prefers async reviews",
                subject="collaboration",
                predicate="style",
                strength=0.55,
                last_updated="2025-02-10T09:00:00Z",
            ),
        ),
    ]
    return PersonaCore(profile=profile, claims=claims)


def _config() -> AppConfig:
    cfg = AppConfig()
    cfg.token_estimator.char_per_token = 4.0
    return cfg


def test_export_persona_markdown_is_deterministic() -> None:
    persona = _sample_persona()
    config = _config()
    options = PersonaExportOptions(seed=123)

    result_a = export_persona_markdown(persona, config=config, options=options)
    result_b = export_persona_markdown(persona, config=config, options=options)

    assert result_a.text == result_b.text
    assert result_a.section_counts["Identity & Roles"] >= 1


def test_export_persona_variant_budgets_scale_tokens() -> None:
    persona = _sample_persona()
    config = _config()

    tiny = export_persona_markdown(
        persona,
        config=config,
        options=PersonaExportOptions(variant=PersonaVariant.TINY),
    )
    full = export_persona_markdown(
        persona,
        config=config,
        options=PersonaExportOptions(variant=PersonaVariant.FULL),
    )

    assert tiny.approx_tokens < full.approx_tokens
    assert "Goals" in full.section_counts


def test_export_persona_sort_modes_reorder_claims() -> None:
    persona = _sample_persona()
    config = _config()

    strength_order = export_persona_markdown(persona, config=config)
    recency_order = export_persona_markdown(
        persona,
        config=config,
        options=PersonaExportOptions(sort_by="recency"),
    )

    assert strength_order.text != recency_order.text
    claims_fragment = recency_order.text.split("## Claims Snapshot", maxsplit=1)[1]
    assert claims_fragment.find("claim.async") < claims_fragment.find("claim.focus")


def test_export_persona_claim_markers_can_be_suppressed() -> None:
    persona = _sample_persona()
    config = _config()

    with_markers = export_persona_markdown(persona, config=config)
    without_markers = export_persona_markdown(
        persona,
        config=config,
        options=PersonaExportOptions(include_claim_markers=False),
    )

    assert "[claim:" in with_markers.text
    assert "[claim:" not in without_markers.text


def test_export_persona_raises_on_empty_profile() -> None:
    persona = PersonaCore(profile={}, claims=[])
    config = _config()

    with pytest.raises(PersonaContentError):
        export_persona_markdown(persona, config=config)
