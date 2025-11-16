"""Persona export helpers for rendering Markdown persona cards."""

from __future__ import annotations

import random
import textwrap
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from aijournal.domain.persona import PersonaCore
from aijournal.io.artifacts import load_artifact_data
from aijournal.utils.paths import resolve_path

if TYPE_CHECKING:
    from pathlib import Path

    from aijournal.common.app_config import AppConfig
    from aijournal.domain.claims import ClaimAtom

__all__ = [
    "PersonaArtifactMissingError",
    "PersonaContentError",
    "PersonaExportError",
    "PersonaExportOptions",
    "PersonaExportResult",
    "PersonaVariant",
    "export_persona_markdown",
    "load_persona_core",
]


class PersonaExportError(RuntimeError):
    """Base error for persona export operations."""


class PersonaArtifactMissingError(PersonaExportError):
    """Raised when persona_core.yaml is missing or unreadable."""


class PersonaContentError(PersonaExportError):
    """Raised when persona_core exists but contains no exportable content."""


class PersonaVariant(StrEnum):
    """Built-in persona export variants."""

    TINY = "tiny"
    SHORT = "short"
    FULL = "full"


VARIANT_BUDGETS = {
    PersonaVariant.TINY: 200,
    PersonaVariant.SHORT: 600,
    PersonaVariant.FULL: 1500,
}

# Section ordering (profile key) that always renders in this order.
SECTION_KEYS: list[tuple[str, str]] = [
    ("Identity & Roles", "traits"),
    ("Core Values", "values_motivations"),
    ("Constraints & Boundaries", "boundaries_ethics"),
    ("Preferences for AI assistants", "coaching_prefs"),
    ("Goals", "goals"),
    ("Work/Thinking Style", "decision_style"),
    ("Habits & Traits", "habits"),
]

# Lower numeric priority means we trim this section last.
SECTION_TRIM_PRIORITY = {
    "Habits & Traits": 6,
    "Work/Thinking Style": 5,
    "Goals": 4,
    "Claims": 4,
    "Preferences for AI assistants": 3,
    "Constraints & Boundaries": 2,
    "Core Values": 1,
    "Identity & Roles": 0,
}

BULLET_CAPS = {
    PersonaVariant.TINY: {
        "Identity & Roles": 2,
        "Core Values": 2,
        "Constraints & Boundaries": 2,
        "Preferences for AI assistants": 3,
        "Goals": 2,
        "Work/Thinking Style": 1,
        "Habits & Traits": 1,
    },
    PersonaVariant.SHORT: {
        "Identity & Roles": 4,
        "Core Values": 5,
        "Constraints & Boundaries": 5,
        "Preferences for AI assistants": 6,
        "Goals": 4,
        "Work/Thinking Style": 4,
        "Habits & Traits": 3,
    },
    PersonaVariant.FULL: {
        "Identity & Roles": 8,
        "Core Values": 8,
        "Constraints & Boundaries": 8,
        "Preferences for AI assistants": 10,
        "Goals": 8,
        "Work/Thinking Style": 7,
        "Habits & Traits": 6,
    },
}


@dataclass(slots=True)
class PersonaExportOptions:
    """Runtime options for Markdown rendering."""

    variant: PersonaVariant = PersonaVariant.SHORT
    token_budget: int | None = None
    sort_by: Literal["strength", "recency", "id"] = "strength"
    deterministic: bool = True
    seed: int | None = None
    max_items: int | None = None
    include_claim_markers: bool = True


@dataclass(slots=True)
class PersonaExportResult:
    """Rendered persona export payload."""

    text: str
    approx_tokens: int
    section_counts: dict[str, int]
    claims_included: int
    budget: int
    budget_exceeded: bool


@dataclass(slots=True)
class _Section:
    name: str
    bullets: list[str]
    min_items: int = 0
    trim_priority: int = 0


KEYS_WITHOUT_PREFIX = {"summary", "detail", "notes", "text"}


def load_persona_core(workspace: Path, config: AppConfig) -> PersonaCore:
    """Load persona_core.yaml for a workspace."""
    persona_path = resolve_path(workspace, config, "derived/persona") / "persona_core.yaml"
    if not persona_path.exists():
        msg = f"Missing {persona_path.relative_to(workspace)}; run 'aijournal persona build' first."
        raise PersonaArtifactMissingError(msg)

    try:
        return load_artifact_data(persona_path, PersonaCore)
    except Exception as exc:  # pragma: no cover - validation-only path
        raise PersonaArtifactMissingError(str(exc)) from exc


def export_persona_markdown(
    persona: PersonaCore,
    *,
    config: AppConfig,
    options: PersonaExportOptions | None = None,
) -> PersonaExportResult:
    """Render persona data to Markdown within an approximate token budget."""
    opts = options or PersonaExportOptions()
    budget = _effective_budget(opts)
    char_per_token = config.token_estimator.char_per_token

    sections = _build_sections(persona, opts)
    if all(not section.bullets for section in sections if section.name != "Claims"):
        msg = "Persona profile is empty; add journal entries or claims first."
        raise PersonaContentError(msg)

    sections = _apply_caps(sections, opts)
    sections = _trim_to_budget(
        sections,
        budget,
        char_per_token,
        include_claim_markers=opts.include_claim_markers,
    )

    markdown = _render_markdown(sections, include_claim_markers=opts.include_claim_markers)
    approx_tokens = _estimate_tokens(markdown, char_per_token)
    budget_exceeded = approx_tokens > budget

    counts = {section.name: len(section.bullets) for section in sections if section.bullets}
    claims_section = next((s for s in sections if s.name == "Claims"), None)
    claims_included = len(claims_section.bullets) if claims_section else 0

    return PersonaExportResult(
        text=markdown,
        approx_tokens=approx_tokens,
        section_counts=counts,
        claims_included=claims_included,
        budget=budget,
        budget_exceeded=budget_exceeded,
    )


def _effective_budget(options: PersonaExportOptions) -> int:
    if options.token_budget is not None:
        return max(32, options.token_budget)
    return VARIANT_BUDGETS.get(options.variant, VARIANT_BUDGETS[PersonaVariant.SHORT])


def _build_sections(persona: PersonaCore, options: PersonaExportOptions) -> list[_Section]:
    sections: list[_Section] = []
    profile = persona.profile or {}

    for name, key in SECTION_KEYS:
        bullets = _extract_profile_bullets(profile.get(key, {}))
        min_items = (
            1
            if name
            in {
                "Identity & Roles",
                "Core Values",
                "Constraints & Boundaries",
                "Preferences for AI assistants",
            }
            else 0
        )
        sections.append(
            _Section(
                name=name,
                bullets=bullets,
                min_items=min_items,
                trim_priority=SECTION_TRIM_PRIORITY.get(name, 5),
            ),
        )

    claim_bullets = _format_claims(persona.claims, options)
    sections.append(
        _Section(
            name="Claims",
            bullets=claim_bullets,
            trim_priority=SECTION_TRIM_PRIORITY.get("Claims", 4),
        ),
    )
    return sections


def _extract_profile_bullets(block: Any) -> list[str]:
    bullets: list[str] = []

    def walk(value: Any, prefix: str = "") -> None:
        if value is None:
            return
        if isinstance(value, str):
            text = value.strip()
            if text:
                bullets.append((prefix + text).strip())
            return
        if isinstance(value, (int, float)):
            bullets.append(f"{prefix}{value}")
            return
        if isinstance(value, list):
            for item in value:
                walk(item, prefix=prefix)
            return
        if isinstance(value, dict):
            for key in sorted(value.keys()):
                item = value[key]
                if key in KEYS_WITHOUT_PREFIX:
                    walk(item, prefix=prefix)
                else:
                    label = f"{prefix}{key}: " if prefix else f"{key}: "
                    walk(item, prefix=label)
            return

    walk(block)
    normalized = []
    for bullet in bullets:
        text = " ".join(bullet.split())
        if text:
            normalized.append(textwrap.shorten(text, width=260, placeholder="…"))
    return normalized


def _format_claims(claims: list[ClaimAtom], options: PersonaExportOptions) -> list[str]:
    if not claims:
        return []

    rng = _claim_rng(options)
    decorated = []
    for claim in claims:
        decorated.append(
            (
                _claim_sort_key(claim, options.sort_by),
                rng.random(),
                claim,
            ),
        )

    decorated.sort(
        key=lambda item: (
            item[0],
            item[1] if not options.deterministic else 0,
            item[2].id,
        ),
    )
    ordered = [item[2] for item in decorated]

    if options.max_items is not None:
        ordered = ordered[: max(0, options.max_items)]

    bullets: list[str] = []
    for claim in ordered:
        marker = f" [claim:{claim.id}]" if options.include_claim_markers else ""
        summary = f"{claim.statement}{marker}".strip()
        extras: list[str] = []
        if claim.subject:
            extras.append(str(claim.subject))
        if claim.predicate:
            extras.append(str(claim.predicate))
        status = str(claim.status or "").lower()
        if status and status != "accepted":
            extras.append(status)
        extras.append(f"strength {float(claim.strength or 0):.2f}")
        bullets.append(f"{summary} — {', '.join(extras)}")
    return bullets


def _claim_rng(options: PersonaExportOptions) -> random.Random:
    if options.seed is not None:
        return random.Random(options.seed)
    if options.deterministic:
        return random.Random(0)
    return random.Random()


def _claim_sort_key(claim: ClaimAtom, mode: str) -> tuple:
    status_priority = {
        "accepted": 0,
        "tentative": 1,
        "rejected": 2,
    }.get(str(claim.status or "").lower(), 1)

    def recency_value() -> float:
        raw = str(claim.provenance.last_updated or "")
        if not raw:
            return 0.0
        try:
            value = datetime.fromisoformat(raw)
            return value.timestamp()
        except ValueError:
            return 0.0

    sort_value: object
    if mode == "recency":
        sort_value = -recency_value()
    elif mode == "id":
        sort_value = claim.id
    else:
        sort_value = -float(claim.strength or 0.0)
    return (sort_value, status_priority)


def _apply_caps(
    sections: list[_Section],
    options: PersonaExportOptions,
) -> list[_Section]:
    caps = BULLET_CAPS.get(options.variant, BULLET_CAPS[PersonaVariant.SHORT])
    budget = options.token_budget
    if budget is not None and budget <= 350:
        caps = BULLET_CAPS[PersonaVariant.TINY]
    elif budget is not None and budget <= 900:
        caps = BULLET_CAPS[PersonaVariant.SHORT]
    elif budget is not None and budget > VARIANT_BUDGETS[PersonaVariant.FULL]:
        caps = {}

    trimmed: list[_Section] = []
    for section in sections:
        limit = caps.get(section.name)
        bullets = list(section.bullets)
        if limit is not None:
            bullets = bullets[:limit]
        trimmed.append(
            _Section(
                name=section.name,
                bullets=bullets,
                min_items=section.min_items,
                trim_priority=section.trim_priority,
            ),
        )
    return trimmed


def _trim_to_budget(
    sections: list[_Section],
    budget: int,
    char_per_token: float,
    *,
    include_claim_markers: bool,
) -> list[_Section]:
    text = _render_markdown(sections, include_claim_markers=include_claim_markers)
    tokens = _estimate_tokens(text, char_per_token)
    if tokens <= budget:
        return sections

    ordered = sorted(sections, key=lambda s: s.trim_priority, reverse=True)
    mutable = [_Section(s.name, list(s.bullets), s.min_items, s.trim_priority) for s in sections]

    def current_tokens() -> int:
        return _estimate_tokens(
            _render_markdown(mutable, include_claim_markers=include_claim_markers),
            char_per_token,
        )

    for _ in range(1000):
        if current_tokens() <= budget:
            break
        candidate = next(
            (section for section in ordered if len(section.bullets) > section.min_items),
            None,
        )
        if candidate is None:
            break
        candidate.bullets.pop()
    return mutable


def _render_markdown(
    sections: list[_Section],
    *,
    include_claim_markers: bool = True,
) -> str:
    lines = ["# Persona Context", ""]
    for section in sections:
        if not section.bullets:
            continue
        if section.name == "Claims":
            lines.append("## Claims Snapshot")
        else:
            lines.append(f"## {section.name}")
        lines.extend(f"- {bullet}" for bullet in section.bullets)
        lines.append("")

    lines.append("## Instructions for the assistant")
    lines.append("- Ground every answer in the identity, values, and boundaries above.")
    lines.append("- Treat 'Never' or 'Avoid' statements as hard constraints unless clarified.")
    if include_claim_markers:
        lines.append("- Mention claim markers like [claim:<id>] when they informed the response.")
    else:
        lines.append("- Reference claims explicitly by their statements when relevant.")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _estimate_tokens(text: str, char_per_token: float) -> int:
    width = char_per_token if char_per_token > 0 else 4.0
    return max(1, int((len(text) / width) + 0.5))
