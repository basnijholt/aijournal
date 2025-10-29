"""Profile command orchestration helpers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import typer
from pydantic import ValidationError

from aijournal.commands.ingest import _load_config, _load_yaml, _use_fake_llm
from aijournal.commands.summarize import (
    _build_meta,
    _entries_to_payload,
    _invoke_structured_llm,
    _json_block,
    _load_normalized_entries,
    _log_entry_progress,
    _validate_timeout,
)
from aijournal.fakes import fake_profile_suggestions
from aijournal.io.yaml_io import load_yaml_model, write_yaml_model
from aijournal.models import (
    ClaimAtom,
    ClaimsFile,
    ClaimSource,
    NormalizedEntry,
    ProfileSuggestions,
    ProfileSuggestionUpdate,
    ProfileSuggestionUpsert,
    Provenance,
    Scope,
    SelfProfile,
    SimpleProfileSuggestionsResponse,
    SimpleSuggestion,
)
from aijournal.pipelines import normalization
from aijournal.services import ClaimConsolidator, ClaimMergeOutcome, LLMResponseError
from aijournal.utils import time as time_utils

DEFAULT_PROFILE_RETRIES = 1


@dataclass(frozen=True)
class InterviewTarget:
    """Candidate facet/claim/prompt ranked for interview follow-ups."""

    path: str
    score: float
    kind: Literal["facet", "claim", "pending"]
    reasons: tuple[str, ...] = ()
    claim_id: str | None = None
    missing_context: tuple[str, ...] = ()


def run_profile_suggest(
    date: str,
    *,
    timeout: float,
    retries: int,
    progress: bool,
) -> Path:
    """Generate profile suggestions for a specific date."""
    root = Path.cwd()
    entries = _load_normalized_entries(root, date)
    if not entries:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    timeout_value = _validate_timeout(timeout)
    _log_entry_progress(f"Generating profile suggestions for {date}", entries, progress)

    profile_model, claim_models = _load_profile_components(root)
    profile = _profile_to_dict(profile_model)
    claims = [claim.model_copy(deep=True) for claim in claim_models]
    if not profile and not claims:
        typer.secho("No profile data", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    config = _load_config(root)
    try:
        suggestions_model = _profile_suggestions_payload(
            entries,
            profile,
            claims,
            date,
            config,
            timeout=timeout_value,
            retries=retries,
        )
    except LLMResponseError as exc:  # pragma: no cover - runtime dependent
        typer.secho(f"Profile suggestions failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    path = _derived_profile_suggestions_path(root, date)
    write_yaml_model(path, suggestions_model)
    return path


def run_profile_apply(
    date: str,
    *,
    suggestions_path: Path | None,
    auto_confirm: bool,
) -> str:
    """Apply previously generated profile suggestions."""
    del auto_confirm  # Reserved for future interactive prompts.

    root = Path.cwd()
    resolved_path = suggestions_path or (root / "derived" / "profile_suggestions" / f"{date}.yaml")
    if not resolved_path.exists():
        typer.secho(
            f"Suggestions file not found: {resolved_path}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    suggestions_model = load_yaml_model(resolved_path, ProfileSuggestions)
    profile_model, claim_models = _load_profile_components(root)
    profile = _profile_to_dict(profile_model)
    claims = [claim.model_copy(deep=True) for claim in claim_models]
    timestamp = time_utils.format_timestamp(time_utils.now())
    changed = False

    for upsert in suggestions_model.upserts:
        if upsert.target == "claims":
            if _apply_claim_upsert(claims, upsert.value, timestamp):
                changed = True

    for update in suggestions_model.updates:
        target = update.target
        if not target:
            continue
        if _apply_profile_update(profile, target, update.value, timestamp):
            changed = True

    if not changed:
        typer.echo("No changes to apply")
        raise typer.Exit(0)

    updated_profile = SelfProfile.model_validate(profile)
    updated_claims = [claim.model_copy(deep=True) for claim in claims]
    write_yaml_model(root / "profile" / "self_profile.yaml", updated_profile)
    write_yaml_model(root / "profile" / "claims.yaml", ClaimsFile(claims=updated_claims))
    return "Applied 1 suggestions file"


def run_profile_status(*, root: Path | None = None) -> None:
    """Show ranked facets/claims requiring review."""
    resolved_root = root or Path.cwd()
    profile_model, claim_models = _load_profile_components(resolved_root)
    profile = _profile_to_dict(profile_model)

    config_path = resolved_root / "config" / "config.yaml"
    config = _load_yaml(config_path) if config_path.exists() else {}
    weights = config.get("impact_weights", {})

    if not profile and not claim_models:
        typer.echo("No profile data")
        raise typer.Exit(0)

    rankings = _compute_rankings(profile, claim_models, weights, time_utils.now())
    if not rankings:
        typer.echo("No profile data")
        raise typer.Exit(0)

    _print_rankings(rankings)


def _derived_profile_suggestions_path(root: Path, day: str) -> Path:
    return root / "derived" / "profile_suggestions" / f"{day}.yaml"


def _profile_suggestions_payload(
    entries: Sequence[NormalizedEntry],
    profile: dict[str, Any],
    claims: Sequence[ClaimAtom],
    date: str,
    config: dict[str, Any],
    *,
    timeout: float | None = None,
    retries: int = DEFAULT_PROFILE_RETRIES,
) -> ProfileSuggestions:
    if _use_fake_llm():
        suggestions = fake_profile_suggestions(
            entries,
            profile,
            claims,
            build_claim=_build_claim_atom_from_entry,
        )
    else:
        simple_response = cast(
            SimpleProfileSuggestionsResponse,
            _invoke_structured_llm(
                "prompts/profile_suggest.md",
                {
                    "date": date,
                    "entries_json": _json_block(_entries_to_payload(entries)),
                    "profile_json": _json_block(profile),
                    "claims_json": _json_block(
                        {"claims": [claim.model_dump(mode="python") for claim in claims]}
                    ),
                },
                response_model=SimpleProfileSuggestionsResponse,
                agent_name="aijournal-profile-suggest",
                config=config,
                timeout=timeout,
                max_attempts=max(1, retries + 1),
                retry_message=(
                    "Return JSON with keys `suggestions` only. Each suggestion must match the "
                    "documented schema and avoid extra fields."
                ),
            ),
        )
        timestamp = time_utils.format_timestamp(time_utils.now())
        suggestions = _simple_suggestions_to_profile(simple_response, timestamp=timestamp)

    suggestions.meta = _build_meta("prompts/profile_suggest.md", config=config)
    return suggestions


def _simple_suggestions_to_profile(
    simple: SimpleProfileSuggestionsResponse,
    *,
    timestamp: str,
) -> ProfileSuggestions:
    upserts: list[ProfileSuggestionUpsert] = []
    updates: list[ProfileSuggestionUpdate] = []

    for suggestion in simple.suggestions:
        kind = (suggestion.kind or "").strip().lower()
        if kind == "claim":
            upsert = _simple_claim_to_upsert(suggestion, timestamp)
            if upsert is not None:
                upserts.append(upsert)
        elif kind == "facet":
            update = _simple_facet_to_update(suggestion)
            if update is not None:
                updates.append(update)
        else:
            typer.secho(
                f"Ignoring unknown suggestion kind: {suggestion.kind}",
                fg=typer.colors.YELLOW,
                err=True,
            )

    return ProfileSuggestions(upserts=upserts, updates=updates)


def _simple_claim_to_upsert(
    suggestion: SimpleSuggestion,
    timestamp: str,
) -> ProfileSuggestionUpsert | None:
    return normalization.simple_claim_to_upsert(suggestion, timestamp)


def _simple_facet_to_update(suggestion: SimpleSuggestion) -> ProfileSuggestionUpdate | None:
    path = (suggestion.facet_path or "").strip()
    if not path or suggestion.value is None:
        typer.secho(
            "Skipping facet suggestion without facet_path or value.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return None

    evidence = [str(entry).strip() for entry in suggestion.evidence if entry]
    return ProfileSuggestionUpdate(
        target=path,
        operation="set",
        value=suggestion.value,
        method="inferred",
        user_verified=False,
        evidence=evidence,
        rationale=suggestion.rationale,
    )


def _build_claim_atom_from_entry(
    entry: NormalizedEntry,
    *,
    claim_id: str,
    statement: str,
    strength: float,
    status: str,
) -> ClaimAtom:
    timestamp = time_utils.format_timestamp(time_utils.now())
    default_sources = [ClaimSource(entry_id=entry.id or claim_id, spans=[])]
    raw = {
        "id": claim_id,
        "type": "preference",
        "subject": entry.title or claim_id,
        "predicate": "insight",
        "value": statement,
        "statement": statement,
        "scope": {
            "domain": None,
            "context": list((entry.tags or [])[:2]),
            "conditions": [],
        },
        "strength": strength,
        "status": status,
        "method": "inferred",
        "user_verified": False,
        "review_after_days": 120,
        "provenance": {
            "sources": [source.model_dump(mode="python") for source in default_sources],
            "first_seen": entry.created_at or timestamp,
        },
    }
    return normalization.normalize_claim_atom(
        raw,
        timestamp=timestamp,
        default_sources=default_sources,
    )


def _load_profile_components(root: Path) -> tuple[SelfProfile | None, list[ClaimAtom]]:
    profile_path = root / "profile" / "self_profile.yaml"
    claims_path = root / "profile" / "claims.yaml"

    profile = load_yaml_model(profile_path, SelfProfile) if profile_path.exists() else None
    if claims_path.exists():
        try:
            claims_file = load_yaml_model(claims_path, ClaimsFile)
            claim_models = list(claims_file.claims)
        except ValidationError:
            raw = _load_yaml(claims_path).get("claims", [])
            claim_models = _claims_to_models(raw if isinstance(raw, list) else [])
    else:
        claim_models = []
    return profile, claim_models


def _profile_to_dict(profile: SelfProfile | None) -> dict[str, Any]:
    return profile.model_dump(mode="python") if profile else {}


def _claims_to_models(claims: Iterable[Any]) -> list[ClaimAtom]:
    normalized: list[ClaimAtom] = []
    timestamp = time_utils.format_timestamp(time_utils.now())
    for raw in claims:
        if not isinstance(raw, (dict, ClaimAtom)):
            continue
        try:
            normalized.append(
                normalization.normalize_claim_atom(
                    raw,
                    timestamp=timestamp,
                ),
            )
        except (ValidationError, ValueError):
            continue
    return normalized


def _apply_profile_update(profile: dict[str, Any], target: str, value: Any, timestamp: str) -> bool:
    parts = target.split(".")
    current = profile
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    key = parts[-1]
    previous = current.get(key)
    if previous == value:
        return False
    current[key] = value
    current["last_updated"] = timestamp
    return True


def _apply_claim_upsert(
    claims: list[ClaimAtom],
    value: ClaimAtom | dict[str, Any],
    timestamp: str,
    events: list[ClaimMergeOutcome] | None = None,
) -> bool:
    try:
        normalized = normalization.normalize_claim_atom(value, timestamp=timestamp)
    except (ValidationError, ValueError):
        return False

    for existing in claims:
        if existing.id == normalized.id and _claims_equivalent(existing, normalized):
            if events is not None:
                events.append(
                    ClaimMergeOutcome(
                        changed=False,
                        action="noop",
                        claim_id=existing.id,
                        delta_strength=0.0,
                    ),
                )
            return False

    consolidator = ClaimConsolidator(timestamp=timestamp)
    outcome = consolidator.upsert(claims, normalized)
    if events is not None:
        events.append(outcome)
    return outcome.changed


_CLAIM_FLOAT_TOLERANCE = 1e-6


def _sanitize_provenance_for_compare(provenance: Provenance) -> dict[str, Any]:
    sanitized = provenance.model_dump(mode="python")
    sanitized.pop("last_updated", None)
    sanitized.pop("observation_count", None)
    return sanitized


def _claim_compare_payload(claim: ClaimAtom) -> dict[str, Any]:
    payload = claim.model_dump(mode="python")
    payload["provenance"] = _sanitize_provenance_for_compare(claim.provenance)
    payload.pop("strength", None)
    return payload


def _structures_equal(lhs: Any, rhs: Any) -> bool:
    if isinstance(lhs, float) and isinstance(rhs, float):
        return abs(lhs - rhs) <= _CLAIM_FLOAT_TOLERANCE
    if isinstance(lhs, dict) and isinstance(rhs, dict):
        if lhs.keys() != rhs.keys():
            return False
        return all(_structures_equal(lhs[key], rhs[key]) for key in lhs)
    if isinstance(lhs, list) and isinstance(rhs, list):
        if len(lhs) != len(rhs):
            return False
        return all(_structures_equal(a, b) for a, b in zip(lhs, rhs))
    return lhs == rhs


def _claims_equivalent(existing: ClaimAtom, incoming: ClaimAtom) -> bool:
    if existing.id != incoming.id:
        return False
    if abs(existing.strength - incoming.strength) > _CLAIM_FLOAT_TOLERANCE:
        return False
    existing_payload = _claim_compare_payload(existing)
    incoming_payload = _claim_compare_payload(incoming)
    return _structures_equal(existing_payload, incoming_payload)


def _coerce_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value.astimezone(UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(value)
    return text if text else None


def _claim_last_updated(claim: ClaimAtom) -> str | None:
    return _coerce_timestamp(claim.provenance.last_updated)


def _impact_for(path: str, weights: dict[str, float]) -> float:
    key = path.split(".", 1)[0]
    return float(weights.get(key, 1.0))


def _collect_entry_tags(entries: Sequence[NormalizedEntry]) -> frozenset[str]:
    tags: set[str] = set()
    for entry in entries:
        for tag in entry.tags or []:
            text = str(tag).strip()
            if text:
                tags.add(text.lower())
    return frozenset(tags)


def _compute_rankings(
    profile: dict[str, Any],
    claims: Sequence[ClaimAtom],
    weights: dict[str, float],
    now: datetime,
    *,
    entries: Sequence[NormalizedEntry] = (),
    pending_prompts: Sequence[str] = (),
) -> list[InterviewTarget]:
    entry_tags = _collect_entry_tags(entries)
    ranked: list[InterviewTarget] = []

    for path, facet in _flatten_facets(profile):
        days = _days_between(now, str(facet.get("last_updated", "")))
        review = facet.get("review_after_days") or 90
        if days is None or review <= 0:
            continue
        staleness = days / float(review)
        base = staleness * _impact_for(path, weights)
        if base <= 0:
            continue
        facet_reasons = [f"staleness={staleness:.2f}×impact"]
        ranked.append(
            InterviewTarget(
                path=path,
                score=base,
                kind="facet",
                reasons=tuple(facet_reasons),
            ),
        )

    claim_weight = float(weights.get("claims", 1.0))
    for claim in claims:
        claim_id = claim.id or "claim"
        days = _days_between(now, _claim_last_updated(claim))
        review = claim.review_after_days or 90
        score = 0.0
        claim_reasons: list[str] = []
        if days is not None and review > 0:
            staleness = days / float(review)
            staleness_score = staleness * claim_weight
            if staleness_score > 0:
                score += staleness_score
                claim_reasons.append(f"staleness={staleness:.2f}")

        status = (claim.status or "tentative").lower()
        if status != "accepted":
            score += 0.4
            claim_reasons.append(f"status={status}")

        strength = float(claim.strength or 0.0)
        if strength < 0.6:
            delta = 0.6 - strength
            score += delta
            claim_reasons.append(f"strength={strength:.2f}")

        scope = claim.scope or Scope()
        scope_tags = {tag.strip().lower() for tag in scope.context if tag.strip()}
        if not scope_tags:
            score += 0.25
            claim_reasons.append("scope missing")

        missing_context = sorted(tag for tag in entry_tags if tag not in scope_tags)
        if missing_context:
            score += min(0.2 * len(missing_context), 0.6)
            claim_reasons.append(f"new_context={', '.join(missing_context[:3])}")

        if score <= 0:
            continue

        ranked.append(
            InterviewTarget(
                path=f"claim:{claim_id}",
                score=score,
                kind="claim",
                reasons=tuple(claim_reasons),
                claim_id=claim.id,
                missing_context=tuple(missing_context[:3]),
            ),
        )

    for idx, prompt in enumerate(pending_prompts, start=1):
        text = str(prompt).strip()
        if not text:
            continue
        ranked.append(
            InterviewTarget(
                path=f"pending:{idx}",
                score=3.0,
                kind="pending",
                reasons=(text,),
            ),
        )

    ranked.sort(key=lambda item: (-item.score, item.path))
    return ranked


def _flatten_facets(node: Any, prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        if "last_updated" in node:
            items.append((prefix or "root", node))
        for key, value in node.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            items.extend(_flatten_facets(value, child_prefix))
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            child_prefix = f"{prefix}[{idx}]"
            items.extend(_flatten_facets(value, child_prefix))
    return items


def _days_between(now: datetime, past: str | None) -> float | None:
    if not past:
        return None
    try:
        candidate = past.replace("Z", "+00:00") if past.endswith("Z") else past
        dt = datetime.fromisoformat(candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
    except ValueError:
        return None
    delta = now - dt
    return delta.total_seconds() / 86400.0


def _print_rankings(ranked: Sequence[InterviewTarget]) -> None:
    if not ranked:
        typer.echo("No profile data")
        return
    typer.echo("Profile review priority:")
    for idx, target in enumerate(ranked, start=1):
        if target.kind == "pending" and target.reasons:
            label = f"pending prompt: {target.reasons[0]}"
        else:
            label = target.path
        typer.echo(f"{idx}. {label} (score {target.score:.2f})")
        for reason in target.reasons:
            typer.echo(f"   - {reason}")


__all__ = [
    "InterviewTarget",
    "run_profile_suggest",
    "run_profile_apply",
    "run_profile_status",
    "_build_claim_atom_from_entry",
    "_apply_claim_upsert",
    "_apply_profile_update",
    "_claim_last_updated",
    "_compute_rankings",
    "_load_profile_components",
    "_profile_to_dict",
]
