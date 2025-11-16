"""Advice command orchestration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from pydantic import BaseModel, ValidationError

from aijournal.commands.pack import _latest_normalized_day
from aijournal.commands.profile import (
    InterviewTarget,
    _compute_rankings,
    load_profile_components,
    profile_to_dict,
)
from aijournal.commands.summarize import (
    _build_meta,
    _json_block,
    _load_normalized_entries,
)
from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.meta import Artifact, ArtifactKind
from aijournal.io.artifacts import load_artifact, save_artifact
from aijournal.models.derived import AdviceCard, ProfileUpdateBatch
from aijournal.pipelines import advise as advise_pipeline
from aijournal.services.ollama import invoke_structured_llm, resolve_model_name
from aijournal.utils import time as time_utils
from aijournal.utils.paths import resolve_path

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aijournal.common.app_config import AppConfig
    from aijournal.common.context import RunContext
    from aijournal.domain.claims import ClaimAtom


class AdviceOptions(BaseModel):
    question: str


@dataclass(slots=True)
class AdvicePrepared:
    question: str
    profile: dict[str, Any]
    claims: list[ClaimAtom]
    rankings: list[InterviewTarget]
    pending_prompts: list[str]


@dataclass(slots=True)
class AdviceResult:
    card: AdviceCard
    question: str
    day: str
    model_name: str


def prepare_inputs(ctx: RunContext, options: AdviceOptions) -> AdvicePrepared:
    profile_model, claim_models = load_profile_components(ctx.workspace, config=ctx.config)
    profile = profile_to_dict(profile_model)
    claims = [claim.model_copy(deep=True) for claim in claim_models]
    if not profile and not claims:
        typer.secho("No profile data", fg=typer.colors.RED, err=True)
        ctx.emit(event="command_failed", reason="no_profile")
        raise typer.Exit(1)

    weights = ctx.config.impact_weights.model_dump(mode="python")
    latest_day = _latest_normalized_day(ctx.workspace, ctx.config)
    entries = _load_normalized_entries(ctx.workspace, ctx.config, latest_day) if latest_day else []
    pending_prompts = _collect_pending_interview_prompts(ctx.workspace, ctx.config)
    rankings = _compute_rankings(
        profile,
        claims,
        weights,
        time_utils.now(),
        entries=entries,
        pending_prompts=pending_prompts,
    )
    ctx.emit(
        event="prepare_summary",
        claims=len(claims),
        rankings=len(rankings),
        pending_prompts=len(pending_prompts),
    )
    return AdvicePrepared(
        question=options.question,
        profile=profile,
        claims=claims,
        rankings=list(rankings),
        pending_prompts=list(pending_prompts),
    )


def invoke_pipeline(ctx: RunContext, prepared: AdvicePrepared) -> AdviceResult:
    advice_card = _advice_payload(
        prepared.question,
        prepared.profile,
        prepared.claims,
        ctx.config,
        rankings=prepared.rankings,
        pending_prompts=prepared.pending_prompts,
        use_fake_llm=ctx.use_fake_llm,
        prompt_set=ctx.prompt_set,
    )
    model_name = resolve_model_name(ctx.config, use_fake_llm=ctx.use_fake_llm)
    day = time_utils.created_date(time_utils.format_timestamp(time_utils.now()))
    ctx.emit(
        event="pipeline_complete",
        recommendations=len(advice_card.recommendations),
        confidence=advice_card.confidence,
    )
    return AdviceResult(
        card=advice_card,
        question=prepared.question,
        day=day,
        model_name=model_name,
    )


def persist_output(ctx: RunContext, result: AdviceResult) -> Path:
    advice_path = _derived_advice_path(ctx.workspace, ctx.config, result.day, result.question)
    artifact_meta = _build_meta(
        "prompts/advise.md",
        model=result.model_name,
        use_fake_llm=ctx.use_fake_llm,
        prompt_kind="advise",
        prompt_set=ctx.prompt_set,
    )
    save_artifact(
        advice_path,
        Artifact[AdviceCard](
            kind=ArtifactKind.ADVICE_CARD,
            meta=artifact_meta,
            data=result.card,
        ),
    )
    ctx.emit(event="artifact_written", path=str(advice_path))
    return advice_path


def run_advise_command(ctx: RunContext, options: AdviceOptions) -> Path:
    return run_command_pipeline(
        ctx,
        options,
        prepare_inputs=prepare_inputs,
        invoke_pipeline=invoke_pipeline,
        persist_output=persist_output,
    )


def run_advise(question: str, workspace: Path | None = None) -> Path:
    """Backward-compatible entrypoint using the current working directory."""
    from aijournal.common.config_loader import load_config, use_fake_llm
    from aijournal.common.context import create_run_context

    workspace = workspace or Path.cwd()
    config = load_config(workspace)
    ctx = create_run_context(
        command="advise",
        workspace=workspace,
        config=config,
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )
    return run_advise_command(ctx, AdviceOptions(question=question))


def _collect_pending_interview_prompts(
    workspace: Path,
    config: AppConfig,
    limit: int = 5,
) -> list[str]:
    directory = resolve_path(workspace, config, "derived/pending") / "profile_updates"
    if not directory.exists():
        return []
    prompts: list[str] = []
    for path in sorted((p for p in directory.glob("*.yaml") if p.is_file()), reverse=True):
        try:
            artifact = load_artifact(path, ProfileUpdateBatch)
        except (ValidationError, ValueError):
            continue
        if artifact.kind is not ArtifactKind.PROFILE_UPDATES:
            continue
        preview = artifact.data.preview
        if not preview:
            continue
        for prompt in preview.interview_prompts:
            text = str(prompt).strip()
            if text and text not in prompts:
                prompts.append(text)
        if len(prompts) >= limit:
            break
    return prompts[:limit]


def _advice_identifier(question: str) -> str:
    day = time_utils.created_date(time_utils.format_timestamp(time_utils.now()))
    digest = sha256(question.encode("utf-8")).hexdigest()[:8]
    return f"adv_{day}_{digest}"


def _advice_payload(
    question: str,
    profile: dict[str, Any],
    claims: Sequence[ClaimAtom],
    config: AppConfig,
    *,
    rankings: Sequence[InterviewTarget],
    pending_prompts: Sequence[str],
    use_fake_llm: bool,
    prompt_set: str | None = None,
) -> AdviceCard:
    rankings_payload = [
        {
            "path": target.path,
            "score": target.score,
            "kind": target.kind,
            "reasons": list(target.reasons),
            "claim_id": target.claim_id,
            "missing_context": list(target.missing_context),
        }
        for target in rankings[:8]
    ]

    llm_advice: AdviceCard | None = None
    if not use_fake_llm:
        llm_advice = invoke_structured_llm(
            "prompts/advise.md",
            {
                "date": time_utils.created_date(time_utils.format_timestamp(time_utils.now())),
                "question": question,
                "profile_json": _json_block(profile),
                "claims_json": _json_block(
                    {"claims": [claim.model_dump(mode="python") for claim in claims]},
                ),
                "rankings_json": _json_block(rankings_payload),
                "pending_prompts_json": _json_block(list(pending_prompts)),
            },
            response_model=AdviceCard,
            agent_name="aijournal-advise",
            config=config,
            prompt_set=prompt_set,
        )

    return advise_pipeline.generate_advice(
        question,
        profile,
        claims,
        use_fake_llm=use_fake_llm,
        advice_identifier=_advice_identifier,
        llm_advice=llm_advice,
        rankings=rankings,
        pending_prompts=pending_prompts,
    )


def _derived_advice_path(workspace: Path, config: AppConfig, day: str, question: str) -> Path:
    slug = time_utils.slugify_title(question)
    return resolve_path(workspace, config, "derived/advice") / day / f"{slug}.yaml"
