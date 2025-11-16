"""Profile command orchestration helpers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import typer
from pydantic import BaseModel, ValidationError

from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.config_loader import load_config, load_yaml, use_fake_llm
from aijournal.common.context import RunContext, create_run_context
from aijournal.common.meta import ArtifactKind
from aijournal.domain.changes import ClaimProposal, FacetChange, ProfileUpdateProposals
from aijournal.domain.claims import (
    ClaimAtom,
    ClaimSource,
    ClaimSourceSpan,
    Provenance,
    Scope,
)
from aijournal.domain.evidence import SourceRef, redact_source_text
from aijournal.io.artifacts import load_artifact, load_artifact_data
from aijournal.io.yaml_io import load_yaml_model, write_yaml_model
from aijournal.models.authoritative import ClaimsFile, SelfProfile
from aijournal.models.derived import ProfileUpdateBatch
from aijournal.pipelines import normalization
from aijournal.services.consolidator import ClaimConsolidator, ClaimMergeOutcome
from aijournal.utils import time as time_utils

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from aijournal.common.app_config import AppConfig
    from aijournal.domain.journal import NormalizedEntry

DEFAULT_PROFILE_RETRIES = 1


class ProfileApplyOptions(BaseModel):
    date: str
    suggestions_path: Path | None = None
    auto_confirm: bool = False


@dataclass(slots=True)
class ProfileApplyPrepared:
    root: Path
    config: AppConfig
    proposals: ProfileUpdateProposals | None
    profile: dict[str, Any] | None
    claims: list[ClaimAtom] | None
    timestamp: str | None
    batch_path: Path | None


@dataclass(slots=True)
class ProfileApplyResult:
    message: str
    changed: bool


class ProfileStatusOptions(BaseModel):
    pass


@dataclass(slots=True)
class ProfileStatusPrepared:
    profile: dict[str, Any]
    claim_models: Sequence[ClaimAtom]
    weights: dict[str, Any]


@dataclass(slots=True)
class ProfileStatusResult:
    persona_status: str
    rankings: list[InterviewTarget]
    reasons: list[str]


@dataclass(frozen=True)
class InterviewTarget:
    """Candidate facet/claim/prompt ranked for interview follow-ups."""

    path: str
    score: float
    kind: Literal["facet", "claim", "pending"]
    reasons: tuple[str, ...] = ()
    claim_id: str | None = None
    missing_context: tuple[str, ...] = ()


def run_profile_apply(
    date: str,
    *,
    suggestions_path: Path | None,
    auto_confirm: bool,
    workspace: Path | None = None,
) -> str:
    """Apply previously generated profile suggestions."""
    workspace = workspace or Path.cwd()
    config = load_config(workspace)
    ctx = create_run_context(
        command="profile.apply",
        workspace=workspace,
        config=config,
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )

    options = ProfileApplyOptions(
        date=date,
        suggestions_path=suggestions_path,
        auto_confirm=auto_confirm,
    )

    return run_profile_apply_command(ctx, options)


def run_profile_status(workspace: Path | None = None, *, root: Path | None = None) -> None:
    """Show ranked facets/claims requiring review."""
    workspace = workspace or Path.cwd()
    config_path = workspace / "config.yaml"
    config = load_yaml(config_path) if config_path.exists() else {}
    ctx = create_run_context(
        command="profile.status",
        workspace=workspace,
        config=config,
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )

    run_profile_status_command(ctx, ProfileStatusOptions())


def _pending_profile_update_dir(workspace: Path, config: AppConfig) -> Path:
    derived = Path(config.paths.derived)
    if not derived.is_absolute():
        derived = workspace / derived
    return derived / "pending" / "profile_updates"


def _latest_profile_update_batch(workspace: Path, config: AppConfig, day: str) -> Path | None:
    directory = _pending_profile_update_dir(workspace, config)
    if not directory.exists():
        return None
    candidates = sorted(directory.glob(f"{day}*.yaml"), reverse=True)
    return candidates[0] if candidates else None


def run_profile_apply_command(
    ctx: RunContext,
    options: ProfileApplyOptions,
) -> str:
    def _prepare(_: RunContext, opts: ProfileApplyOptions) -> ProfileApplyPrepared:
        candidate: Path | None
        if opts.suggestions_path is not None:
            candidate = opts.suggestions_path
        else:
            candidate = _latest_profile_update_batch(ctx.workspace, ctx.config, opts.date)
        if candidate is None or not candidate.exists():
            typer.secho(
                "Profile update batch not found; pass --file with a pending batch path.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1)

        batch_path: Path | None = None
        proposals: ProfileUpdateProposals | None = None
        try:
            artifact = load_artifact(candidate, ProfileUpdateBatch)
            if artifact.kind is ArtifactKind.PROFILE_UPDATES:
                batch_path = candidate
        except Exception:
            proposals = load_artifact_data(candidate, ProfileUpdateProposals)

        if batch_path is not None:
            return ProfileApplyPrepared(
                root=ctx.workspace,
                config=ctx.config,
                proposals=None,
                profile=None,
                claims=None,
                timestamp=None,
                batch_path=batch_path,
            )

        if proposals is None:
            typer.secho(
                "File is not a profile update batch or legacy proposals artifact.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1)

        profile_model, claim_models = load_profile_components(ctx.workspace, config=ctx.config)
        profile = profile_to_dict(profile_model)
        claims = [claim.model_copy(deep=True) for claim in claim_models]
        timestamp = time_utils.format_timestamp(time_utils.now())
        ctx.emit(
            event="prepare_summary",
            claims=len(claims),
            facet_changes=len(proposals.facets),
        )
        return ProfileApplyPrepared(
            root=ctx.workspace,
            config=ctx.config,
            proposals=proposals,
            profile=profile,
            claims=claims,
            timestamp=timestamp,
            batch_path=None,
        )

    def _invoke(_: RunContext, prepared: ProfileApplyPrepared) -> ProfileApplyResult:
        if prepared.batch_path is not None:
            from aijournal.services.capture import utils as capture_utils

            applied = capture_utils.apply_profile_update_batch(
                prepared.root,
                prepared.config,
                prepared.batch_path,
            )
            if not applied:
                typer.echo("No changes to apply")
                raise typer.Exit(0)
            return ProfileApplyResult(message="Applied profile update batch", changed=True)

        assert prepared.proposals is not None
        assert prepared.profile is not None
        assert prepared.claims is not None
        assert prepared.timestamp is not None
        changed = False
        events: list[ClaimMergeOutcome] = []

        for claim_proposal in prepared.proposals.claims:
            if _apply_claim_proposal(prepared.claims, claim_proposal, prepared.timestamp, events):
                changed = True

        for facet_change in prepared.proposals.facets:
            if _apply_facet_change(prepared.profile, facet_change, prepared.timestamp):
                changed = True

        if not changed:
            typer.echo("No changes to apply")
            raise typer.Exit(0)

        updated_profile = SelfProfile.model_validate(prepared.profile)
        updated_claims = [claim.model_copy(deep=True) for claim in prepared.claims]
        profile_dir = Path(ctx.config.paths.profile)
        if not profile_dir.is_absolute():
            profile_dir = ctx.workspace / profile_dir
        write_yaml_model(profile_dir / "self_profile.yaml", updated_profile)
        write_yaml_model(profile_dir / "claims.yaml", ClaimsFile(claims=updated_claims))
        return ProfileApplyResult(message="Applied legacy suggestions file", changed=True)

    def _persist(_: RunContext, result: ProfileApplyResult) -> str:
        return result.message

    return run_command_pipeline(
        ctx,
        options,
        prepare_inputs=_prepare,
        invoke_pipeline=_invoke,
        persist_output=_persist,
    )


def run_profile_status_command(ctx: RunContext, options: ProfileStatusOptions) -> None:
    def _prepare(_: RunContext, __: ProfileStatusOptions) -> ProfileStatusPrepared:
        profile_model, claim_models = load_profile_components(ctx.workspace, config=ctx.config)
        profile = profile_to_dict(profile_model)

        weights = ctx.config.impact_weights.model_dump(mode="python")

        return ProfileStatusPrepared(
            profile=profile,
            claim_models=tuple(claim_models),
            weights=weights,
        )

    def _invoke(_: RunContext, prepared: ProfileStatusPrepared) -> ProfileStatusResult:
        if not prepared.profile and not prepared.claim_models:
            typer.echo("No profile data")
            raise typer.Exit(0)

        rankings = _compute_rankings(
            prepared.profile,
            prepared.claim_models,
            prepared.weights,
            time_utils.now(),
        )
        if not rankings:
            typer.echo("No profile data")
            raise typer.Exit(0)

        return ProfileStatusResult(
            persona_status="available",
            rankings=rankings,
            reasons=[],
        )

    def _persist(_: RunContext, result: ProfileStatusResult) -> None:
        _print_rankings(result.rankings)

    run_command_pipeline(
        ctx,
        options,
        prepare_inputs=_prepare,
        invoke_pipeline=_invoke,
        persist_output=_persist,
    )


def _sanitize_proposals(proposals: ProfileUpdateProposals) -> ProfileUpdateProposals:
    sanitized_claims = [
        proposal.model_copy(
            update={"evidence": [redact_source_text(ref) for ref in proposal.evidence]},
        )
        for proposal in proposals.claims
    ]
    sanitized_facets = [
        change.model_copy(update={"evidence": [redact_source_text(ref) for ref in change.evidence]})
        for change in proposals.facets
    ]
    return proposals.model_copy(update={"claims": sanitized_claims, "facets": sanitized_facets})


def _apply_claim_proposal(
    claims: list[ClaimAtom],
    proposal: ClaimProposal,
    timestamp: str,
    events: list[ClaimMergeOutcome] | None = None,
) -> bool:
    existing_ids = {claim.id for claim in claims if claim.id}
    claim_atom = _claim_proposal_to_atom(proposal, timestamp, existing_ids)
    if claim_atom is None:
        return False
    return apply_claim_upsert(claims, claim_atom, timestamp, events)


def _proposal_claim_id(
    proposal: ClaimProposal,
    statement: str,
    existing_ids: set[str] | None = None,
) -> str:
    """Derive a stable, unique ID per statement/normalized_id combination."""
    _ = existing_ids  # retained for backward compatibility; collisions are deterministic
    normalized_statement = " ".join(statement.split()).lower()
    digest = hashlib.sha256(normalized_statement.encode("utf-8")).hexdigest()[:8]
    normalized_ids = [cid.strip() for cid in proposal.normalized_ids if cid]
    if normalized_ids:
        base = normalized_ids[0]
    else:
        slug = time_utils.slugify_title(statement) or "claim"
        base = f"proposal-{slug}"
    suffix = f"-{digest}"
    max_base_len = max(1, 96 - len(suffix))
    trimmed_base = base[:max_base_len].rstrip("-")
    if trimmed_base:
        return f"{trimmed_base}{suffix}"
    return f"claim-{digest}"[:96]


def _apply_facet_change(profile: dict[str, Any], change: FacetChange, timestamp: str) -> bool:
    path = (change.path or "").strip()
    if not path:
        return False
    operation = (change.operation or "set").strip().lower()
    if operation == "remove":
        return _remove_profile_path(profile, path, timestamp)
    return apply_profile_update(profile, path, change.value, timestamp)


def _claim_proposal_to_atom(
    proposal: ClaimProposal,
    timestamp: str,
    existing_ids: set[str] | None = None,
) -> ClaimAtom | None:
    statement = proposal.statement.strip()
    if not statement:
        typer.secho("Skipping claim proposal without statement.", fg=typer.colors.YELLOW, err=True)
        return None

    claim_id = _proposal_claim_id(proposal, statement, existing_ids)

    evidence_sources = _source_refs_to_claim_sources(
        proposal.evidence,
        proposal.normalized_ids,
    )
    if not evidence_sources:
        evidence_sources = [ClaimSource(entry_id=claim_id, spans=[])]

    raw_claim = {
        "id": claim_id,
        "type": proposal.type,
        "subject": proposal.subject,
        "predicate": proposal.predicate,
        "value": proposal.value,
        "statement": proposal.statement,
        "scope": proposal.scope.model_dump(mode="python"),
        "strength": proposal.strength,
        "status": proposal.status,
        "method": proposal.method,
        "user_verified": proposal.user_verified,
        "review_after_days": proposal.review_after_days,
        "provenance": {
            "sources": [source.model_dump(mode="python") for source in evidence_sources],
            "first_seen": time_utils.created_date(timestamp),
            "last_updated": timestamp,
            "observation_count": max(1, len(evidence_sources)),
        },
    }

    try:
        return normalization.normalize_claim_atom(
            raw_claim,
            timestamp=timestamp,
            default_sources=evidence_sources,
        )
    except ValidationError:
        typer.secho(
            "Skipping claim proposal that could not be normalized.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return None


def _remove_profile_path(profile: dict[str, Any], target: str, timestamp: str) -> bool:
    parts = target.split(".")
    if not parts:
        return False
    current = profile
    parents: list[dict[str, Any]] = []
    for part in parts[:-1]:
        node = current.get(part)
        if not isinstance(node, dict):
            return False
        parents.append(node)
        current = node
    key = parts[-1]
    if key not in current:
        return False
    del current[key]
    current["last_updated"] = timestamp
    for parent in parents:
        parent.setdefault("last_updated", timestamp)
    return True


def _source_refs_to_claim_sources(
    evidence: Sequence[SourceRef],
    fallback_ids: Sequence[str],
) -> list[ClaimSource]:
    sources: list[ClaimSource] = []
    seen: set[str] = set()

    for ref in evidence:
        sanitized_ref = redact_source_text(ref)
        entry_id = (sanitized_ref.entry_id or "").strip()
        if not entry_id or entry_id in seen:
            continue
        spans = [
            ClaimSourceSpan(
                type=span.type,
                index=span.index,
                start=span.start,
                end=span.end,
            )
            for span in sanitized_ref.spans or []
        ]
        sources.append(ClaimSource(entry_id=entry_id, spans=spans))
        seen.add(entry_id)

    for fallback in fallback_ids:
        entry_id = (fallback or "").strip()
        if not entry_id or entry_id in seen:
            continue
        sources.append(ClaimSource(entry_id=entry_id, spans=[]))
        seen.add(entry_id)

    return sources


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
    sanitized_sources = [
        ClaimSource.model_validate(
            redact_source_text(source).model_dump(mode="python"),
        )
        for source in default_sources
    ]
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
            "sources": [source.model_dump(mode="python") for source in sanitized_sources],
            "first_seen": entry.created_at or timestamp,
        },
    }
    return normalization.normalize_claim_atom(
        raw,
        timestamp=timestamp,
        default_sources=sanitized_sources,
    )


def load_profile_components(
    root: Path,
    *,
    config: AppConfig,
) -> tuple[SelfProfile | None, list[ClaimAtom]]:
    """Load profile metadata and claim atoms for a workspace.

    Args:
        root: Workspace directory root.
        config: Application configuration containing path settings.

    """
    profile_dir = Path(config.paths.profile)
    if not profile_dir.is_absolute():
        profile_dir = root / profile_dir

    profile_path = profile_dir / "self_profile.yaml"
    claims_path = profile_dir / "claims.yaml"

    profile = load_yaml_model(profile_path, SelfProfile) if profile_path.exists() else None
    if claims_path.exists():
        try:
            claims_file = load_yaml_model(claims_path, ClaimsFile)
            claim_models = list(claims_file.claims)
        except ValidationError:
            raw = load_yaml(claims_path).get("claims", [])
            claim_models = _claims_to_models(raw if isinstance(raw, list) else [])
    else:
        claim_models = []
    return profile, claim_models


def profile_to_dict(profile: SelfProfile | None) -> dict[str, Any]:
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


def apply_profile_update(profile: dict[str, Any], target: str, value: Any, timestamp: str) -> bool:
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


def apply_claim_upsert(
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
                        action="update",
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
    if "sources" in sanitized and isinstance(sanitized["sources"], list):
        sanitized["sources"] = [
            redact_source_text(SourceRef.model_validate(source)).model_dump(mode="python")
            for source in sanitized["sources"]
        ]
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
