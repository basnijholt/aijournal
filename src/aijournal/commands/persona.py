"""Persona command orchestration helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from pydantic import BaseModel

from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.config_loader import use_fake_llm
from aijournal.common.context import RunContext, create_run_context
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.claims import ClaimAtom  # noqa: TC001
from aijournal.domain.persona import PersonaCore
from aijournal.io.artifacts import load_artifact, save_artifact
from aijournal.pipelines import persona as persona_pipeline
from aijournal.utils import time as time_utils
from aijournal.utils.coercion import coerce_float
from aijournal.utils.paths import resolve_path

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aijournal.common.app_config import AppConfig

PERSONA_DEFAULTS = {
    "token_budget": 1200,
    "max_claims": 24,
    "min_claims": 8,
}

DEFAULT_CHAR_PER_TOKEN = 4.2


class PersonaBuildOptions(BaseModel):
    profile: dict[str, Any]
    claims: list[ClaimAtom]
    token_budget_override: int | None = None
    max_claims_override: int | None = None
    min_claims_override: int | None = None


@dataclass(slots=True)
class PersonaPrepared:
    profile: dict[str, Any]
    claims: list[ClaimAtom]
    token_budget: int
    max_claims: int
    min_claims: int
    char_per_token: float
    impact_weights: dict[str, Any]
    now: datetime


@dataclass(slots=True)
class PersonaResult:
    artifact: Artifact[PersonaCore]
    path: Path
    changed: bool


def prepare_inputs(ctx: RunContext, options: PersonaBuildOptions) -> PersonaPrepared:
    if not options.profile and not options.claims:
        typer.secho(
            "No profile data or claims available; run `aijournal init` or add entries first.",
            fg=typer.colors.RED,
            err=True,
        )
        ctx.emit(event="command_failed", reason="no_profile_data")
        raise typer.Exit(1)

    persona_cfg = ctx.config.persona
    token_budget = int(
        options.token_budget_override
        if options.token_budget_override is not None
        else persona_cfg.token_budget,
    )
    max_claims = int(
        options.max_claims_override
        if options.max_claims_override is not None
        else persona_cfg.max_claims,
    )
    min_claims = int(
        options.min_claims_override
        if options.min_claims_override is not None
        else persona_cfg.min_claims,
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

    char_per_token = ctx.config.token_estimator.char_per_token

    impact_weights = ctx.config.impact_weights.model_dump(mode="python")
    now_dt = time_utils.now()

    ctx.emit(
        event="prepare_summary",
        token_budget=token_budget,
        max_claims=max_claims,
        min_claims=min_claims,
        claims=len(options.claims),
    )

    claims_copy = [claim.model_copy(deep=True) for claim in options.claims]
    return PersonaPrepared(
        profile=options.profile,
        claims=claims_copy,
        token_budget=token_budget,
        max_claims=max_claims,
        min_claims=min_claims,
        char_per_token=char_per_token,
        impact_weights=impact_weights,
        now=now_dt,
    )


def invoke_pipeline(ctx: RunContext, prepared: PersonaPrepared) -> PersonaResult:
    try:
        persona_result = persona_pipeline.build_persona_core(
            prepared.profile,
            prepared.claims,
            token_budget=prepared.token_budget,
            max_claims=prepared.max_claims,
            min_claims=prepared.min_claims,
            char_per_token=prepared.char_per_token,
            impact_weights=prepared.impact_weights,
            now=prepared.now,
        )
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    persona_core = persona_result.persona
    persona_claim_models = [claim.model_copy(deep=True) for claim in persona_core.claims]
    selection = persona_result.selection
    ranked_claims = persona_result.ranked_claims

    sources: dict[str, str] = {}
    profile_path = resolve_path(ctx.workspace, ctx.config, "profile/self_profile.yaml")
    claims_path = resolve_path(ctx.workspace, ctx.config, "profile/claims.yaml")
    if profile_path.exists():
        sources["profile"] = _relative_to_root(profile_path, ctx.workspace)
    if claims_path.exists():
        sources["claims"] = _relative_to_root(claims_path, ctx.workspace)
    source_mtimes = _persona_source_mtimes(ctx.workspace, ctx.workspace, ctx.config)

    persona_path = resolve_path(ctx.workspace, ctx.config, "derived/persona") / "persona_core.yaml"
    existing_artifact = None
    if persona_path.exists():
        try:
            existing_artifact = load_artifact(persona_path, PersonaCore)
        except Exception:
            existing_artifact = None

    artifact_meta = _persona_artifact_meta(
        generated_at=time_utils.format_timestamp(prepared.now),
        token_budget=prepared.token_budget,
        planned_tokens=selection.planned_tokens,
        char_per_token=prepared.char_per_token,
        selection_strategy="strength*impact*decay",
        trimmed_ids=selection.trimmed_ids,
        claim_pool=len(ranked_claims),
        claim_count=len(persona_claim_models),
        max_claims=prepared.max_claims,
        min_claims=prepared.min_claims,
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
            mode="json",
        ) != artifact_meta.model_dump(mode="json")
    else:
        changed = True

    ctx.emit(
        event="pipeline_complete",
        claim_count=len(persona_claim_models),
        trimmed=len(selection.trimmed_ids),
        changed=changed,
    )
    return PersonaResult(artifact=artifact, path=persona_path, changed=changed)


def persist_output(ctx: RunContext, result: PersonaResult) -> tuple[Path, bool]:
    del ctx
    save_artifact(result.path, result.artifact)
    return result.path, result.changed


def run_persona_build_command(ctx: RunContext, options: PersonaBuildOptions) -> tuple[Path, bool]:
    return run_command_pipeline(
        ctx,
        options,
        prepare_inputs=prepare_inputs,
        invoke_pipeline=invoke_pipeline,
        persist_output=persist_output,
    )


def _relative_to_root(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _profile_yaml_paths(workspace: Path, config: AppConfig) -> list[Path]:
    profile_dir = resolve_path(workspace, config, "profile")
    if not profile_dir.exists():
        return []
    return sorted(p for p in profile_dir.glob("*.yaml") if p.is_file())


def _persona_source_mtimes(root: Path, workspace: Path, config: AppConfig) -> dict[str, float]:
    state: dict[str, float] = {}
    for path in _profile_yaml_paths(workspace, config):
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


def persona_state(root: Path, workspace: Path, config: AppConfig) -> tuple[str, list[str]]:
    persona_path = resolve_path(workspace, config, "derived/persona") / "persona_core.yaml"
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

    current_state = _persona_source_mtimes(root, workspace, config)
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


def ensure_persona_ready_for_pack(root: Path, workspace: Path, config: AppConfig) -> None:
    status, reasons = persona_state(root, workspace, config)
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
    config: AppConfig,
    root: Path | None = None,
    token_budget_override: int | None = None,
    max_claims_override: int | None = None,
    min_claims_override: int | None = None,
) -> tuple[Path, bool]:
    root = root or Path.cwd()
    ctx = create_run_context(
        command="persona.build",
        workspace=root,
        config=config,
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )
    options = PersonaBuildOptions(
        profile=profile,
        claims=list(claim_models),
        token_budget_override=token_budget_override,
        max_claims_override=max_claims_override,
        min_claims_override=min_claims_override,
    )
    return run_persona_build_command(ctx, options)
