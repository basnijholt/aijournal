"""Advice command orchestration helpers."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import typer
from pydantic import ValidationError

from aijournal.commands.ingest import _load_config, _use_fake_llm
from aijournal.commands.pack import _latest_normalized_day
from aijournal.commands.profile import (
    InterviewTarget,
    _compute_rankings,
    load_profile_components,
    profile_to_dict,
)
from aijournal.commands.summarize import (
    _artifact_meta_from_summary,
    _build_meta,
    _invoke_structured_llm,
    _json_block,
    _load_normalized_entries,
)
from aijournal.common.meta import Artifact, ArtifactKind
from aijournal.domain.claims import ClaimAtom
from aijournal.io.artifacts import load_artifact, save_artifact
from aijournal.models.derived import AdviceCard, ProfileUpdateBatch
from aijournal.pipelines import advise as advise_pipeline
from aijournal.services.ollama import build_ollama_config_from_mapping
from aijournal.utils import time as time_utils


def run_advise(question: str) -> Path:
    """Generate advice from the current profile and return the output path."""
    root = Path.cwd()
    profile_model, claim_models = load_profile_components(root)
    profile = profile_to_dict(profile_model)
    claims = [claim.model_copy(deep=True) for claim in claim_models]
    if not profile and not claims:
        typer.secho("No profile data", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    config = _load_config(root)
    weights = config.get("impact_weights", {})
    latest_day = _latest_normalized_day(root)
    entries = _load_normalized_entries(root, latest_day) if latest_day else []
    pending_prompts = _collect_pending_interview_prompts(root)
    rankings = _compute_rankings(
        profile,
        claims,
        weights,
        time_utils.now(),
        entries=entries,
        pending_prompts=pending_prompts,
    )
    advice_card = _advice_payload(
        question,
        profile,
        claims,
        config,
        rankings=rankings,
        pending_prompts=pending_prompts,
    )
    model_name = (
        "fake-ollama" if _use_fake_llm() else build_ollama_config_from_mapping(config).model
    )
    day = time_utils.created_date(time_utils.format_timestamp(time_utils.now()))
    advice_path = _derived_advice_path(root, day, question)
    summary_meta = _build_meta("prompts/advise.md", model=model_name)
    artifact_meta = _artifact_meta_from_summary(summary_meta)
    save_artifact(
        advice_path,
        Artifact[AdviceCard](
            kind=ArtifactKind.ADVICE_CARD,
            meta=artifact_meta,
            data=advice_card,
        ),
    )
    return advice_path


def _collect_pending_interview_prompts(root: Path, limit: int = 5) -> list[str]:
    directory = root / "derived" / "pending" / "profile_updates"
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
    config: dict[str, Any],
    *,
    rankings: Sequence[InterviewTarget],
    pending_prompts: Sequence[str],
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

    def request_advice() -> AdviceCard:
        return cast(
            AdviceCard,
            _invoke_structured_llm(
                "prompts/advise.md",
                {
                    "date": time_utils.created_date(time_utils.format_timestamp(time_utils.now())),
                    "question": question,
                    "profile_json": _json_block(profile),
                    "claims_json": _json_block(
                        {"claims": [claim.model_dump(mode="python") for claim in claims]}
                    ),
                    "rankings_json": _json_block(rankings_payload),
                    "pending_prompts_json": _json_block(list(pending_prompts)),
                },
                response_model=AdviceCard,
                agent_name="aijournal-advise",
                config=config,
                max_attempts=2,
                retry_message=(
                    "Return JSON with keys `id`, `query`, `assumptions`, `recommendations`, "
                    "`tradeoffs`, `next_actions`, `confidence`, `alignment`, `style`."
                ),
            ),
        )

    return advise_pipeline.generate_advice(
        question,
        profile,
        claims,
        use_fake_llm=_use_fake_llm(),
        advice_identifier=_advice_identifier,
        request_advice=request_advice,
        rankings=rankings,
        pending_prompts=pending_prompts,
    )


def _derived_advice_path(root: Path, day: str, question: str) -> Path:
    slug = time_utils.slugify_title(question)
    return root / "derived" / "advice" / day / f"{slug}.yaml"
