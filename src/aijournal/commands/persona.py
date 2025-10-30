"""Persona command orchestration helpers."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.claims import ClaimAtom
from aijournal.domain.persona import PersonaCore
from aijournal.io.artifacts import load_artifact, save_artifact
from aijournal.pipelines import persona as persona_pipeline
from aijournal.utils import time as time_utils
from aijournal.utils.coercion import coerce_float

PERSONA_DEFAULTS = {
    "token_budget": 1200,
    "max_claims": 24,
    "min_claims": 8,
}

DEFAULT_CHAR_PER_TOKEN = 4.2


def _relative_to_root(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _profile_yaml_paths(root: Path) -> list[Path]:
    profile_dir = root / "profile"
    if not profile_dir.exists():
        return []
    return sorted(p for p in profile_dir.glob("*.yaml") if p.is_file())


def _persona_source_mtimes(root: Path) -> dict[str, float]:
    state: dict[str, float] = {}
    for path in _profile_yaml_paths(root):
        rel = _relative_to_root(path, root)
        state[rel] = round(path.stat().st_mtime, 6)
    return state


def _persona_artifact_meta(
    *,
    generated_at: str,
    token_budget: int,
    planned_tokens: int,
    char_per_token: float,
    selection_strategy: str,
    trimmed_ids: Sequence[str],
    claim_pool: int,
    claim_count: int,
    max_claims: int,
    min_claims: int,
    budget_exceeded: bool,
    sources: dict[str, str],
    source_mtimes: dict[str, float],
) -> ArtifactMeta:
    trimmed_payload = (
        json.dumps(
            [{"type": "claim", "id": claim_id} for claim_id in trimmed_ids],
            sort_keys=True,
            separators=(",", ":"),
        )
        if trimmed_ids
        else ""
    )
    notes: dict[str, str] = {
        "token_budget": str(token_budget),
        "planned_tokens": str(planned_tokens),
        "selection_strategy": selection_strategy,
        "trimmed": trimmed_payload,
        "claim_pool": str(claim_pool),
        "claim_count": str(claim_count),
        "max_claims": str(max_claims),
        "min_claims": str(min_claims),
        "budget_exceeded": json.dumps(bool(budget_exceeded)),
        "source_mtimes": json.dumps(source_mtimes, sort_keys=True, separators=(",", ":")),
    }
    # Drop empty placeholders to keep notes compact.
    notes = {key: value for key, value in notes.items() if value not in {"", "{}", "[]"}}
    source_map = {**sources} if sources else {}
    return ArtifactMeta(
        created_at=generated_at or time_utils.format_timestamp(time_utils.now()),
        model=None,
        prompt_path=None,
        prompt_hash=None,
        char_per_token=char_per_token,
        notes=notes or None,
        sources=source_map or None,
    )


def persona_state(root: Path) -> tuple[str, list[str]]:
    persona_path = root / "derived" / "persona" / "persona_core.yaml"
    if not persona_path.exists():
        rel = _relative_to_root(persona_path, root)
        return "missing", [f"Missing {rel}; run `aijournal persona build`."]

    try:
        persona_artifact = load_artifact(persona_path, PersonaCore)
    except Exception as exc:  # pragma: no cover - depends on file contents
        return (
            "stale",
            [f"Persona core failed validation ({exc.__class__.__name__}); rebuild to refresh."],
        )

    notes = persona_artifact.meta.notes or {}
    source_mtimes_raw = notes.get("source_mtimes")
    stored_raw: dict[str, float] = {}
    if source_mtimes_raw:
        try:
            parsed = json.loads(source_mtimes_raw)
            if isinstance(parsed, dict):
                stored_raw = {str(key): float(value) for key, value in parsed.items()}
        except (ValueError, TypeError):
            stored_raw = {}
    if not stored_raw:
        return (
            "stale",
            [
                "Persona core lacks source_mtimes metadata; rebuild once to capture profile state.",
            ],
        )

    current_state = _persona_source_mtimes(root)
    reasons: list[str] = []
    for rel, current_mtime in current_state.items():
        stored_value = stored_raw.get(rel)
        stored_mtime = coerce_float(stored_value)
        if stored_mtime is None:
            reasons.append(f"New profile file detected: {rel}")
            continue
        if abs(current_mtime - stored_mtime) > 1e-6:
            reasons.append(
                f"{rel} modified at {datetime.fromtimestamp(current_mtime, tz=UTC):%Y-%m-%d %H:%M:%SZ} "
                f"(was {datetime.fromtimestamp(stored_mtime, tz=UTC):%Y-%m-%d %H:%M:%SZ}).",
            )

    for rel in stored_raw:
        if rel not in current_state:
            reasons.append(f"{rel} missing; it existed when persona core was generated.")

    if reasons:
        return "stale", reasons
    return "fresh", []


def ensure_persona_ready_for_pack(root: Path) -> None:
    status, reasons = persona_state(root)
    if status == "missing":
        typer.secho(
            "Persona core not found. Run `aijournal persona build` before assembling packs.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    if status == "stale":
        typer.secho(
            "Persona core is stale; re-run `aijournal persona build` to refresh profile changes.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        for reason in reasons:
            typer.echo(f"- {reason}", err=True)


def run_persona_build(
    profile: dict[str, Any],
    claim_models: Sequence[ClaimAtom],
    *,
    config: dict[str, Any],
    root: Path | None = None,
    token_budget_override: int | None = None,
    max_claims_override: int | None = None,
    min_claims_override: int | None = None,
) -> tuple[Path, bool]:
    root = root or Path.cwd()
    if not profile and not claim_models:
        typer.secho(
            "No profile data or claims available; run `aijournal init` or add entries first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    persona_cfg_raw = config.get("persona")
    persona_cfg = persona_cfg_raw if isinstance(persona_cfg_raw, dict) else {}
    token_budget = int(
        token_budget_override
        if token_budget_override is not None
        else persona_cfg.get("token_budget") or PERSONA_DEFAULTS["token_budget"]
    )
    max_claims = int(
        max_claims_override
        if max_claims_override is not None
        else persona_cfg.get("max_claims") or PERSONA_DEFAULTS["max_claims"]
    )
    min_claims = int(
        min_claims_override
        if min_claims_override is not None
        else persona_cfg.get("min_claims") or PERSONA_DEFAULTS["min_claims"]
    )
    if token_budget <= 0:
        typer.secho("Token budget must be positive", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if max_claims <= 0:
        typer.secho("max-claims must be positive", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if min_claims < 0 or min_claims > max_claims:
        typer.secho("min-claims must be between 0 and max-claims", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    token_estimator_raw = config.get("token_estimator")
    token_estimator = token_estimator_raw if isinstance(token_estimator_raw, dict) else {}
    char_per_token = coerce_float(token_estimator.get("char_per_token")) or DEFAULT_CHAR_PER_TOKEN

    now_dt = time_utils.now()
    impact_weights_raw = config.get("impact_weights")
    impact_weights = impact_weights_raw if isinstance(impact_weights_raw, dict) else {}
    try:
        persona_result = persona_pipeline.build_persona_core(
            profile,
            claim_models,
            token_budget=token_budget,
            max_claims=max_claims,
            min_claims=min_claims,
            char_per_token=char_per_token,
            impact_weights=impact_weights,
            now=now_dt,
        )
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    generated_at = time_utils.format_timestamp(now_dt)
    persona_core = persona_result.persona
    persona_claim_models = [claim.model_copy(deep=True) for claim in persona_core.claims]
    selection = persona_result.selection
    ranked_claims = persona_result.ranked_claims

    sources: dict[str, str] = {}
    profile_path = root / "profile" / "self_profile.yaml"
    claims_path = root / "profile" / "claims.yaml"
    if profile_path.exists():
        sources["profile"] = _relative_to_root(profile_path, root)
    if claims_path.exists():
        sources["claims"] = _relative_to_root(claims_path, root)
    source_mtimes = _persona_source_mtimes(root)

    persona_path = root / "derived" / "persona" / "persona_core.yaml"
    existing_artifact = None
    if persona_path.exists():
        try:
            existing_artifact = load_artifact(persona_path, PersonaCore)
        except Exception:
            existing_artifact = None

    artifact_meta = _persona_artifact_meta(
        generated_at=generated_at,
        token_budget=token_budget,
        planned_tokens=selection.planned_tokens,
        char_per_token=char_per_token,
        selection_strategy="strength*impact*decay",
        trimmed_ids=selection.trimmed_ids,
        claim_pool=len(ranked_claims),
        claim_count=len(persona_claim_models),
        max_claims=max_claims,
        min_claims=min_claims,
        budget_exceeded=selection.budget_exceeded,
        sources=sources,
        source_mtimes=source_mtimes,
    )
    artifact = Artifact[PersonaCore](
        kind=ArtifactKind.PERSONA_CORE,
        meta=artifact_meta,
        data=persona_core,
    )
    if existing_artifact is not None:
        changed = existing_artifact.data != persona_core or existing_artifact.meta.model_dump(
            mode="json"
        ) != artifact_meta.model_dump(mode="json")
    else:
        changed = True
    save_artifact(persona_path, artifact)
    return persona_path, changed
