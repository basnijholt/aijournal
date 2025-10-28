"""Typer CLI entrypoint for aijournal.

This module intentionally keeps only Typer glue and lightweight interactive
helpers. Command orchestration now lives under ``aijournal.commands``; any
remaining utilities here support interactive previews that still require direct
terminal IO.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import httpx
import typer
import yaml
from pydantic import ValidationError
from typer.models import CommandInfo

from aijournal.commands import summarize as summarize_commands
from aijournal.commands.advise import (
    _collect_pending_interview_prompts,
    run_advise,
)
from aijournal.commands.characterize import (
    _normalize_claim_proposals,
    _pending_updates_dir,
    run_characterize,
)
from aijournal.commands.chat import run_chat
from aijournal.commands.chatd import run_chatd
from aijournal.commands.facts import (
    run_facts as run_facts_command,
)
from aijournal.commands.index import (
    run_index_rebuild,
    run_index_search,
    run_index_tail,
)
from aijournal.commands.ingest import (
    _load_config,
    _parse_entry,
    _relative_source_path,
    _use_fake_llm,
    _write_yaml_if_changed,
)
from aijournal.commands.ingest import (
    run_ingest as run_ingest_command,
)
from aijournal.commands.init import run_init as run_init_command
from aijournal.commands.new import run_new as run_new_command
from aijournal.commands.pack import run_pack
from aijournal.commands.persona import (
    persona_state,
    run_persona_build,
)
from aijournal.commands.profile import (
    InterviewTarget,
    _apply_claim_upsert,
    _apply_profile_update,
    _compute_rankings,
    _load_profile_components,
    _profile_to_dict,
    run_profile_apply,
    run_profile_status,
    run_profile_suggest,
)
from aijournal.commands.summarize import (
    _entries_to_payload,
    _json_block,
    _load_normalized_entries,
)
from aijournal.commands.summarize import (
    _invoke_structured_llm as _commands_invoke_structured_llm,
)
from aijournal.commands.summarize import (
    _structured_call_with_retry as _commands_structured_call_with_retry,
)
from aijournal.commands.summarize import (
    run_summarize as run_summarize_command,
)
from aijournal.io.yaml_io import load_yaml_model, write_yaml_model
from aijournal.models import (
    ClaimAtom,
    ClaimConflictPayload,
    ClaimPreviewEvent,
    ClaimProposal,
    ClaimsFile,
    ClaimSignaturePayload,
    FacetProposal,
    InterviewQuestion,
    InterviewSet,
    NormalizedEntry,
    ProfileUpdateBatch,
    ProfileUpdatePreview,
    Scope,
    SelfProfile,
)
from aijournal.pipelines import normalization
from aijournal.services import (
    ClaimConflict,
    ClaimConsolidator,
    ClaimMergeOutcome,
    ClaimSignature,
    LLMResponseError,
    build_ollama_config_from_mapping,
    resolve_ollama_host,
    run_ollama_agent,
)
from aijournal.utils import time as time_utils
from aijournal.utils.coercion import coerce_int
from aijournal.utils.paths import (
    find_data_root,
    normalized_entry_path,
)

app = typer.Typer(help="Local-first personal journal utilities.")
profile_app = typer.Typer(help="Profile utilities.")
ollama_app = typer.Typer(help="Ollama helpers.")
index_app = typer.Typer(help="Retrieval index utilities.")
persona_app = typer.Typer(help="Persona utilities.")

app.add_typer(profile_app, name="profile")
app.add_typer(ollama_app, name="ollama")
app.add_typer(index_app, name="index")
app.add_typer(persona_app, name="persona")

# Phase 1 scaffold: advanced operations namespace and placeholder groups.
ops_app = typer.Typer(help="Advanced operations namespace.")
ops_pipeline_app = typer.Typer(help="Pipeline tools (normalize, summarize, characterize).")
ops_profile_app = typer.Typer(help="Profile management utilities.")
ops_index_app = typer.Typer(help="Index maintenance utilities.")
ops_persona_app = typer.Typer(help="Persona workflows.")
ops_feedback_app = typer.Typer(help="Feedback processing utilities.")
ops_system_app = typer.Typer(help="System diagnostics and doctor helpers.")
ops_dev_app = typer.Typer(help="Developer fixtures and helpers.")

ops_app.add_typer(ops_pipeline_app, name="pipeline")
ops_app.add_typer(ops_profile_app, name="profile")
ops_app.add_typer(ops_index_app, name="index")
ops_app.add_typer(ops_persona_app, name="persona")
ops_app.add_typer(ops_feedback_app, name="feedback")
ops_app.add_typer(ops_system_app, name="system")
ops_app.add_typer(ops_dev_app, name="dev")

app.add_typer(ops_app, name="ops")

export_app = typer.Typer(help="Context export utilities.")
serve_app = typer.Typer(help="Service runners and daemons.")

app.add_typer(export_app, name="export")
app.add_typer(serve_app, name="serve")


@app.callback()
def main() -> None:
    """Aijournal command-line interface."""
    # Intentionally empty; commands provide functionality.
    return


PENDING_UPDATES_SUBDIR = "derived/pending/profile_updates"

HIGH_IMPACT_PROBES = [
    "- Top 3 values you refuse to trade off—rank them.",
    "- One long-term goal that matters most this year—and why now?",
    "- When speed and quality conflict, what do you choose by default?",
    "- List 2 anti-goals (things you want to avoid) and the reasons.",
    "- Your risk posture in career moves: low / medium / high—why?",
    "- Energy map: when are you best for deep work vs admin?",
    "- Feedback style you prefer when you’re wrong?",
    "- Three coping strategies that reliably help under stress.",
]


DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_LLM_RETRIES = 1


def _normalize_created_at(value: Any) -> str:
    return normalization.normalize_created_at(value)


def _invoke_structured_llm(
    prompt_path: str,
    variables: dict[str, str],
    *,
    response_model: type[Any],
    agent_name: str,
    config: dict[str, Any],
    timeout: float | None = None,
) -> Any:
    """Proxy to summarize command helper while honoring patched runners."""

    original_runner = summarize_commands.run_ollama_agent
    original_builder = summarize_commands.build_ollama_config_from_mapping
    summarize_commands.run_ollama_agent = run_ollama_agent
    summarize_commands.build_ollama_config_from_mapping = build_ollama_config_from_mapping
    try:
        return _commands_invoke_structured_llm(
            prompt_path,
            variables,
            response_model=response_model,
            agent_name=agent_name,
            config=config,
            timeout=timeout,
        )
    finally:
        summarize_commands.build_ollama_config_from_mapping = original_builder
        summarize_commands.run_ollama_agent = original_runner


def _structured_call_with_retry(
    func: Any,
    *,
    retries: int,
    label: str,
) -> Any:
    return _commands_structured_call_with_retry(func, retries=retries, label=label)


def _summarize_day_payload(
    entries: Sequence[NormalizedEntry],
    date: str,
    config: dict[str, Any],
    *,
    timeout: float | None,
    retries: int,
) -> Any:
    """Proxy to the summarize command helper with test-friendly overrides."""

    return summarize_commands._summarize_day_payload(
        entries,
        date,
        config,
        timeout=timeout,
        retries=retries,
        invoke_structured_llm=_invoke_structured_llm,
        structured_call=_structured_call_with_retry,
        use_fake_llm=_use_fake_llm(),
    )


def _latest_pending_batch(root: Path) -> Path | None:
    directory = _pending_updates_dir(root)
    if not directory.exists():
        return None
    files = sorted(p for p in directory.glob("*.yaml") if p.is_file())
    return files[-1] if files else None


@app.command()
def init(
    path: Path | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Directory to initialize (defaults to current working directory).",
    ),
) -> None:
    """Initialize the local aijournal layout."""
    summary = run_init_command(path)
    typer.echo(summary)


@app.command()
def new(
    title: str | None = typer.Argument(
        None,
        help="Title for the journal entry; omit when using --fake.",
    ),
    tags: list[str] | None = typer.Option(
        None,
        "--tags",
        "-t",
        help="Tag to attach to the entry (repeatable).",
    ),
    fake: int = typer.Option(
        0,
        "--fake",
        min=0,
        help="Generate N fake entries with deterministic metadata (no LLM).",
    ),
    seed: int | None = typer.Option(
        None,
        "--seed",
        help="Optional RNG seed for --fake generation.",
    ),
) -> None:
    """Create a new journal entry or synthesize fake entries for testing."""
    run_new_command(title, tags, fake, seed)


@app.command()
def ingest(
    sources: list[Path] = typer.Argument(
        ...,
        exists=True,
        dir_okay=True,
        file_okay=True,
        readable=True,
        resolve_path=True,
        help="Markdown files or directories to ingest.",
    ),
    source_type: str = typer.Option(
        "external",
        "--source-type",
        help="Label recorded in the manifest for these sources.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Maximum number of files to ingest.",
    ),
    snapshot: bool = typer.Option(
        True,
        "--snapshot/--no-snapshot",
        help="Store raw copies under data/raw/<hash>.md.",
    ),
) -> None:
    """Ingest Markdown posts into normalized YAML via Ollama."""
    run_ingest_command(
        sources,
        source_type=source_type,
        limit=limit,
        snapshot=snapshot,
    )


@app.command()
def normalize(
    entry: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Path to journal Markdown entry.",
    ),
) -> None:
    """Normalize a Markdown journal entry into structured YAML."""
    entry = entry.resolve()
    try:
        frontmatter, sections = _parse_entry(entry)
    except ValueError as err:
        typer.secho(str(err), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    entry_id_value = frontmatter.get("id")
    created_value = frontmatter.get("created_at")
    title_value = frontmatter.get("title")
    tags = frontmatter.get("tags", []) or []

    if not all([entry_id_value, created_value, title_value]):
        typer.secho(
            "Frontmatter must include id, created_at, title.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    entry_id = str(entry_id_value)
    title = str(title_value)
    created_str = _normalize_created_at(created_value)
    date_str = time_utils.created_date(created_str)
    root = find_data_root(entry)
    normalized_data = {
        "id": entry_id,
        "created_at": created_str,
        "source_path": _relative_source_path(entry, root),
        "title": title,
        "tags": tags,
        "sections": sections,
    }

    output_path = normalized_entry_path(root, date_str, entry_id)
    _write_yaml_if_changed(
        output_path,
        normalized_data,
        schema="normalized_entry",
    )
    typer.echo(str(output_path))


@app.command()
def summarize(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to summarize."),
    timeout: float = typer.Option(
        DEFAULT_TIMEOUT_SECONDS,
        "--timeout",
        help="Seconds to wait for the LLM response before retrying.",
        show_default=True,
    ),
    retries: int = typer.Option(
        DEFAULT_LLM_RETRIES,
        "--retries",
        min=0,
        help="Number of retry attempts when the model times out or returns invalid JSON.",
        show_default=True,
    ),
    progress: bool = typer.Option(
        False,
        "--progress/--no-progress",
        help="Print progress for each normalized entry before calling the model.",
    ),
) -> None:
    """Generate a daily summary from normalized entries."""
    summary_path = run_summarize_command(
        date,
        timeout=timeout,
        retries=retries,
        progress=progress,
    )
    typer.echo(str(summary_path))


@app.command()
def facts(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to analyze."),
    timeout: float = typer.Option(
        DEFAULT_TIMEOUT_SECONDS,
        "--timeout",
        help="Seconds to wait for the LLM response before retrying.",
        show_default=True,
    ),
    retries: int = typer.Option(
        DEFAULT_LLM_RETRIES,
        "--retries",
        min=0,
        help="Number of retry attempts when the model times out or returns invalid JSON.",
        show_default=True,
    ),
    progress: bool = typer.Option(
        False,
        "--progress/--no-progress",
        help="Print progress for each normalized entry before calling the model.",
    ),
) -> None:
    """Generate micro-facts from normalized entries."""
    root = Path.cwd()
    _, claim_models = _load_profile_components(root)
    preview, facts_path = run_facts_command(
        date,
        timeout=timeout,
        retries=retries,
        progress=progress,
        claim_models=claim_models,
        build_claim_preview=lambda proposals, claims, timestamp: _build_claim_preview(
            proposals,
            claims,
            timestamp=timestamp,
        ),
    )
    if preview:
        _print_claim_preview(preview)
    typer.echo(str(facts_path))


@profile_app.command("suggest")
def profile_suggest(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to analyze."),
    timeout: float = typer.Option(
        DEFAULT_TIMEOUT_SECONDS,
        "--timeout",
        help="Seconds to wait for the LLM response before retrying.",
        show_default=True,
    ),
    retries: int = typer.Option(
        DEFAULT_LLM_RETRIES,
        "--retries",
        min=0,
        help="Number of retry attempts when the model times out or returns invalid JSON.",
        show_default=True,
    ),
    progress: bool = typer.Option(
        False,
        "--progress/--no-progress",
        help="Print progress for each normalized entry before calling the model.",
    ),
) -> None:
    """Suggest profile updates based on normalized entries."""
    path = run_profile_suggest(
        date,
        timeout=timeout,
        retries=retries,
        progress=progress,
    )
    typer.echo(str(path))


@profile_app.command("apply")
def profile_apply(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to apply."),
    file: Path | None = typer.Option(None, "--file", help="Path to suggestions YAML."),
    yes: bool = typer.Option(False, "--yes", help="Apply without prompting."),
) -> None:
    """Apply profile suggestions to authoritative files (offline)."""
    message = run_profile_apply(
        date,
        suggestions_path=file,
        auto_confirm=yes,
    )
    typer.echo(message)


@app.command()
def characterize(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to analyze."),
    timeout: float = typer.Option(
        DEFAULT_TIMEOUT_SECONDS,
        "--timeout",
        help="Seconds to wait for the LLM response before retrying.",
        show_default=True,
    ),
    retries: int = typer.Option(
        DEFAULT_LLM_RETRIES,
        "--retries",
        min=0,
        help="Number of retry attempts when the model times out or returns invalid JSON.",
        show_default=True,
    ),
    progress: bool = typer.Option(
        False,
        "--progress/--no-progress",
        help="Print progress for each normalized entry before calling the model.",
    ),
) -> None:
    """Derive pending profile updates from normalized entries."""
    batch_path = run_characterize(
        date,
        timeout=timeout,
        retries=retries,
        progress=progress,
        build_claim_preview=lambda proposals, claims, ts: _build_claim_preview(
            proposals,
            claims,
            timestamp=ts,
        ),
        normalize_claims=_normalize_claim_proposals,
        invoke_structured_llm=_invoke_structured_llm,
        structured_call=_structured_call_with_retry,
    )
    typer.echo(str(batch_path))


@app.command("review-updates")
def review_updates(
    file: Path | None = typer.Option(
        None,
        "--file",
        help="Specific pending batch to review (defaults to latest).",
    ),
    apply: bool = typer.Option(False, "--apply", help="Apply the proposed updates."),
) -> None:
    """Review or apply pending profile update batches."""
    root = Path.cwd()
    batch_path = file or _latest_pending_batch(root)
    if batch_path is None:
        typer.secho("No pending profile update batches found.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if not batch_path.exists():
        typer.secho(f"Batch file not found: {batch_path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    batch = load_yaml_model(batch_path, ProfileUpdateBatch)
    claim_proposals: list[ClaimProposal] = [
        proposal.model_copy(deep=True) for proposal in batch.proposals.claims
    ]
    facet_proposals: list[FacetProposal] = [
        proposal.model_copy(deep=True) for proposal in batch.proposals.facets
    ]

    batch_id = batch.batch_id or batch_path.stem
    typer.echo(
        f"Batch {batch_id}: {len(claim_proposals)} claim(s), {len(facet_proposals)} facet(s)",
    )

    for claim_proposal in claim_proposals:
        typer.echo(f"- claim {claim_proposal.claim.id}: {claim_proposal.claim.statement}")

    for facet_proposal in facet_proposals:
        if facet_proposal.path:
            typer.echo(f"- facet {facet_proposal.path}: {facet_proposal.value}")

    if not apply:
        if batch.preview and batch.preview.claim_events:
            _print_claim_preview(batch.preview)
        else:
            _preview_claim_consolidation(root, claim_proposals)
        if batch.preview and batch.preview.interview_prompts:
            typer.echo("Hint: run `aijournal interview` to follow up on the queued prompts.")
        return

    profile_model, claim_models = _load_profile_components(root)
    profile = _profile_to_dict(profile_model)
    claims_data = [claim.model_copy(deep=True) for claim in claim_models]
    timestamp = time_utils.format_timestamp(time_utils.now())
    applied = 0
    merge_events: list[ClaimMergeOutcome] = []

    for claim_proposal in claim_proposals:
        if _apply_claim_upsert(claims_data, claim_proposal.claim, timestamp, events=merge_events):
            applied += 1

    for facet_proposal in facet_proposals:
        if not facet_proposal.path:
            continue
        if _apply_profile_update(profile, facet_proposal.path, facet_proposal.value, timestamp):
            applied += 1

    if not applied:
        typer.echo("No changes applied")
        return

    updated_profile = SelfProfile.model_validate(profile)
    updated_claims = [claim.model_copy(deep=True) for claim in claims_data]
    write_yaml_model(root / "profile" / "self_profile.yaml", updated_profile)
    write_yaml_model(root / "profile" / "claims.yaml", ClaimsFile(claims=updated_claims))
    _emit_claim_merge_events(merge_events, "Applied claim consolidations:")
    typer.echo(f"Applied {applied} updates from {batch_path}")


@app.command()
def advise(
    question: str = typer.Argument(..., help="Question for the advisor to answer."),
) -> None:
    """Generate advice from the current profile."""
    advice_path = run_advise(question)
    typer.echo(str(advice_path))


@ollama_app.command("health")
def ollama_health() -> None:
    """Inspect Ollama availability for both fake and live modes."""
    if _use_fake_llm():
        models = [
            {"name": "llama3.1:8b-instruct", "size": "8B", "quant": "Q4_K_M"},
            {"name": "llama3.1:70b-instruct", "size": "70B", "quant": "Q4_K_M"},
        ]
        payload = {
            "endpoint": "fake://ollama",
            "default": models[0]["name"],
            "models": models,
        }
        typer.echo(yaml.safe_dump(payload, sort_keys=False).rstrip())
        return

    host = os.getenv("AIJOURNAL_OLLAMA_HOST")
    base = resolve_ollama_host(host)
    try:
        response = httpx.get(f"{base}/api/tags", timeout=15.0)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:  # pragma: no cover - depends on runtime host
        typer.secho(f"Unable to query Ollama: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    models_raw = data.get("models") if isinstance(data, dict) else None
    models_payload: list[dict[str, Any]] = []
    if isinstance(models_raw, list):
        for item in models_raw:
            item_data = item if isinstance(item, dict) else {}
            models_payload.append(
                {
                    "name": item_data.get("name") or item_data.get("model"),
                    "size": item_data.get("size"),
                    "digest": item_data.get("digest"),
                    "modified_at": item_data.get("modified_at") or item_data.get("last_modified"),
                },
            )

    root = Path.cwd()
    config = _load_config(root)
    payload = {
        "endpoint": base,
        "default": build_ollama_config_from_mapping(config).model,
        "models": models_payload,
    }
    typer.echo(yaml.safe_dump(payload, sort_keys=False).rstrip())


@persona_app.command("build")
def persona_build(
    token_budget: int | None = typer.Option(
        None,
        help="Override the persona_core token budget (default 1200).",
    ),
    max_claims: int | None = typer.Option(
        None,
        help="Limit the number of claims considered for persona core.",
    ),
    min_claims: int | None = typer.Option(
        None,
        help="Guarantee at least this many claims remain even if over budget.",
    ),
) -> None:
    """Regenerate derived/persona/persona_core.yaml."""
    root = Path.cwd()
    profile_model, claim_models = _load_profile_components(root)
    profile = _profile_to_dict(profile_model)
    config = _load_config(root)
    path, changed = run_persona_build(
        profile,
        claim_models,
        config=config,
        root=root,
        token_budget_override=token_budget,
        max_claims_override=max_claims,
        min_claims_override=min_claims,
    )
    status = "Wrote" if changed else "Persona core already up to date"
    typer.echo(f"{status}: {path}")


@persona_app.command("status")
def persona_status() -> None:
    """Check whether persona_core.yaml matches the latest profile edits."""
    root = Path.cwd()
    status, reasons = persona_state(root)
    if status == "fresh":
        typer.echo("Persona core is up to date (profile files unchanged).")
        return

    heading = "Persona core missing" if status == "missing" else "Persona core is stale"
    color = typer.colors.RED if status == "missing" else typer.colors.YELLOW
    typer.secho(heading, fg=color, err=True)
    for reason in reasons:
        typer.echo(f"- {reason}", err=True)
    typer.echo("Run `aijournal persona build` to refresh.")
    raise typer.Exit(1)


def _build_targeted_probes(
    targets: Sequence[InterviewTarget],
    entries: Sequence[NormalizedEntry],
    *,
    max_items: int = 4,
) -> InterviewSet:
    title = "recent notes"
    if entries:
        first = entries[0]
        title = first.title or first.id or title

    questions: list[InterviewQuestion] = []
    for idx, target in enumerate(targets, start=1):
        if len(questions) >= max_items:
            break
        if target.kind == "pending" and target.reasons:
            prompt_text = target.reasons[0]
            questions.append(
                InterviewQuestion(
                    id=f"pending-{idx}",
                    text=prompt_text,
                    target_facet=target.path,
                    priority="high",
                ),
            )
            continue

        if target.kind == "claim":
            label = target.claim_id or target.path
            if target.missing_context:
                context_label = target.missing_context[0]
                text = f"How does {label} hold when context includes '{context_label}'?"
            else:
                text = f"What new evidence from {title} should update {label}?"
        else:
            text = f"What fresh detail from {title} should refine {target.path}?"

        if target.reasons:
            text += f" ({'; '.join(target.reasons[:2])})"

        questions.append(
            InterviewQuestion(
                id=f"ranked-{idx}",
                text=text,
                target_facet=target.path,
                priority="high" if target.score >= 1.5 else "medium",
            ),
        )
        if len(questions) >= max_items:
            break

    if len(questions) < 2:
        return InterviewSet()
    return InterviewSet(questions=questions)


def _signature_payload_from_claim(claim: ClaimAtom) -> ClaimSignaturePayload:
    scope = claim.scope or Scope()
    return ClaimSignaturePayload(
        claim_type=str(claim.type or "preference"),
        subject=str(claim.subject or ""),
        predicate=str(claim.predicate or ""),
        domain=scope.domain,
        context=[item for item in scope.context if item],
        conditions=[item for item in scope.conditions if item],
    )


def _signature_payload_from_signature(signature: ClaimSignature) -> ClaimSignaturePayload:
    domain, context, conditions = signature.scope
    return ClaimSignaturePayload(
        claim_type=signature.claim_type,
        subject=signature.subject,
        predicate=signature.predicate,
        domain=domain,
        context=[item for item in context if item],
        conditions=[item for item in conditions if item],
    )


def _conflict_payload_from_outcome(conflict: ClaimConflict) -> ClaimConflictPayload:
    return ClaimConflictPayload(
        claim_id=conflict.claim_id,
        signature=_signature_payload_from_signature(conflict.signature),
        statement=conflict.statement,
        existing_value=conflict.existing_value,
        incoming_value=conflict.incoming_value,
        incoming_sources=[source.model_copy(deep=True) for source in conflict.incoming_sources],
    )


def _format_scope_label(scope: tuple[str | None, tuple[str, ...], tuple[str, ...]]) -> str:
    domain, context, conditions = scope
    parts: list[str] = []
    if domain:
        parts.append(str(domain))
    if context:
        parts.append("/".join(context))
    if conditions:
        parts.append("|".join(conditions))
    return " :: ".join(parts) if parts else "global"


def _emit_claim_merge_events(events: list[ClaimMergeOutcome], heading: str) -> None:
    relevant = [event for event in events if event.action != "noop"]
    if not relevant:
        return
    typer.echo(heading)
    for event in relevant:
        if event.action == "created":
            typer.echo(f"  • new claim {event.claim_id}")
        elif event.action == "merged":
            typer.echo(f"  • merged {event.claim_id} (Δstrength {event.delta_strength:+0.2f})")
        elif event.action == "conflict" and event.conflict:
            conflict = event.conflict
            scope_label = _format_scope_label(conflict.signature.scope)
            typer.secho(
                (
                    f"  • conflict {event.claim_id} [{scope_label}]: "
                    f"'{conflict.existing_value}' vs '{conflict.incoming_value}'"
                ),
                fg=typer.colors.YELLOW,
            )
        elif event.action == "scope_split":
            existing_scope = (
                _format_scope_label(event.conflict.signature.scope) if event.conflict else "global"
            )
            related_scope = (
                _format_scope_label(event.related_signature.scope)
                if event.related_signature
                else "global"
            )
            target = event.related_claim_id or "new-claim"
            typer.echo(
                f"  • scope split {event.claim_id} [{existing_scope}] -> {target} [{related_scope}]"
            )


def _preview_claim_consolidation(
    root: Path,
    claim_proposals: Sequence[Any],
) -> None:
    if not claim_proposals:
        return
    _, claim_models = _load_profile_components(root)
    if not claim_models:
        return
    timestamp = time_utils.format_timestamp(time_utils.now())
    working_claims = [claim.model_copy(deep=True) for claim in claim_models]
    consolidator = ClaimConsolidator(timestamp=timestamp)
    events: list[ClaimMergeOutcome] = []
    for proposal in claim_proposals:
        if isinstance(proposal, ClaimProposal):
            incoming = proposal.claim.model_copy(deep=True)
        elif isinstance(proposal, dict):
            raw_claim = proposal.get("claim") if isinstance(proposal, dict) else None
            if raw_claim is None:
                continue
            try:
                incoming = normalization.normalize_claim_atom(raw_claim, timestamp=timestamp)
            except (ValidationError, ValueError):
                continue
        else:
            continue
        outcome = consolidator.upsert(working_claims, incoming)
        if outcome.action != "noop":
            events.append(outcome)
    _emit_claim_merge_events(events, "Preview (claim consolidation):")


def _build_claim_preview(
    claim_proposals: Sequence[ClaimProposal],
    existing_claims: Sequence[ClaimAtom],
    *,
    timestamp: str,
) -> ProfileUpdatePreview | None:
    if not claim_proposals:
        return None

    working_claims = [claim.model_copy(deep=True) for claim in existing_claims]
    consolidator = ClaimConsolidator(timestamp=timestamp)
    events: list[ClaimPreviewEvent] = []
    prompts: list[str] = []

    for proposal in claim_proposals:
        incoming = proposal.claim.model_copy(deep=True)
        outcome = consolidator.upsert(working_claims, incoming)
        if outcome.action == "noop":
            continue
        signature_payload = (
            _signature_payload_from_signature(outcome.signature)
            if outcome.signature
            else _signature_payload_from_claim(incoming)
        )
        related_signature_payload = (
            _signature_payload_from_signature(outcome.related_signature)
            if outcome.related_signature
            else None
        )
        conflict_payload = None
        if outcome.conflict:
            conflict_payload = _conflict_payload_from_outcome(outcome.conflict)
            scope_label = _format_scope_label(outcome.conflict.signature.scope)
            prompts.append(
                f"Clarify claim {outcome.claim_id} [{scope_label}]: "
                f"existing='{outcome.conflict.existing_value}' vs incoming='{outcome.conflict.incoming_value}'."
            )
        events.append(
            ClaimPreviewEvent(
                action=outcome.action,
                claim_id=outcome.claim_id,
                delta_strength=float(outcome.delta_strength or 0.0),
                statement=incoming.statement,
                value=incoming.value,
                strength=float(incoming.strength or 0.0),
                signature=signature_payload,
                conflict=conflict_payload,
                related_claim_id=outcome.related_claim_id,
                related_action=outcome.related_action,
                related_signature=related_signature_payload,
            )
        )

    if not events and not prompts:
        return None
    return ProfileUpdatePreview(claim_events=events, interview_prompts=prompts)


def _scope_tuple_from_payload(
    signature: ClaimSignaturePayload | None,
) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    if signature is None:
        return (None, tuple(), tuple())
    return (
        signature.domain,
        tuple(signature.context),
        tuple(signature.conditions),
    )


def _print_claim_preview(preview: ProfileUpdatePreview) -> None:
    events = [event for event in preview.claim_events if event.action != "noop"]
    if events:
        typer.echo("Preview (claim consolidation):")
        for event in events:
            scope_label = _format_scope_label(_scope_tuple_from_payload(event.signature))
            if event.action == "created":
                typer.echo(f"  • new claim {event.claim_id} [{scope_label}]")
            elif event.action == "merged":
                typer.echo(
                    (
                        f"  • merged {event.claim_id} [{scope_label}] "
                        f"(Δstrength {event.delta_strength:+0.2f})"
                    ),
                )
            elif event.action == "scope_split":
                new_scope_label = _format_scope_label(
                    _scope_tuple_from_payload(event.related_signature),
                )
                target = event.related_claim_id or "new-claim"
                action_note = f" ({event.related_action})" if event.related_action else ""
                typer.echo(
                    (
                        f"  • scope split {event.claim_id} [{scope_label}] -> "
                        f"{target} [{new_scope_label}]{action_note}"
                    ),
                )
            elif event.action == "conflict" and event.conflict:
                conflict = event.conflict
                scope_label = _format_scope_label(
                    (
                        conflict.signature.domain,
                        tuple(conflict.signature.context),
                        tuple(conflict.signature.conditions),
                    ),
                )
                typer.secho(
                    (
                        f"  • conflict {event.claim_id} [{scope_label}]: "
                        f"'{conflict.existing_value}' vs '{conflict.incoming_value}'"
                    ),
                    fg=typer.colors.YELLOW,
                )
            else:
                typer.echo(f"  • {event.action} {event.claim_id} [{scope_label}]")

    if preview.interview_prompts:
        typer.echo("Follow-up interviews queued:")
        for prompt in preview.interview_prompts:
            typer.echo(f"  • {prompt}")


@profile_app.command("status")
def profile_status() -> None:
    """Show ranked facets/claims needing review."""
    run_profile_status()


@app.command("profile-status")
def profile_status_alias() -> None:
    """Alias command for profile status (for backwards compatibility)."""
    run_profile_status()


@app.command("interview")
def interview(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to review."),
) -> None:
    """Surface targeted interview probes based on stale facets."""
    root = Path.cwd()
    profile_model, claim_models = _load_profile_components(root)
    profile = _profile_to_dict(profile_model)
    claims = [claim.model_copy(deep=True) for claim in claim_models]
    if not profile and not claims:
        typer.secho("No profile data", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    entries = _load_normalized_entries(root, date)
    if not entries:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    config = _load_config(root)
    weights = config.get("impact_weights", {})

    max_questions = _coaching_max_questions(profile)
    pending_prompts = _collect_pending_interview_prompts(root)
    rankings = _compute_rankings(
        profile,
        claims,
        weights,
        time_utils.now(),
        entries=entries,
        pending_prompts=pending_prompts,
    )

    if max_questions == 0:
        typer.echo("Interview probes:")
        typer.echo("- Coaching preferences disable probing right now.")
        return

    if _use_fake_llm():
        interview_set = _build_targeted_probes(rankings, entries, max_items=max_questions)
    else:
        rankings_payload = [
            {
                "path": target.path,
                "score": target.score,
                "kind": target.kind,
                "reasons": list(target.reasons),
                "claim_id": target.claim_id,
                "missing_context": list(target.missing_context),
            }
            for target in rankings[: max(max_questions * 2, 6)]
        ]
        try:
            interview_set = cast(
                InterviewSet,
                _structured_call_with_retry(
                    lambda: _invoke_structured_llm(
                        "prompts/interview.md",
                        {
                            "date": date,
                            "profile_json": _json_block(profile),
                            "claims_json": _json_block(
                                {"claims": [claim.model_dump(mode="python") for claim in claims]}
                            ),
                            "entries_json": _json_block(_entries_to_payload(entries)),
                            "rankings_json": _json_block(rankings_payload),
                            "coaching_prefs_json": _json_block(profile.get("coaching_prefs", {})),
                        },
                        response_model=InterviewSet,
                        agent_name="aijournal-interview",
                        config=config,
                    ),
                    retries=0,
                    label="interview",
                ),
            )
        except LLMResponseError as exc:
            typer.secho(
                f"Interview generation failed ({exc}); falling back to heuristic probes.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            interview_set = InterviewSet()
        if interview_set.questions:
            interview_set.questions = interview_set.questions[:max_questions]

    if not interview_set.questions:
        interview_set = _build_targeted_probes(rankings, entries, max_items=max_questions)

    if not interview_set.questions:
        fallback_questions = [
            InterviewQuestion(
                id=f"default-{idx + 1}",
                text=probe.lstrip("- ").strip(),
                priority="baseline",
            )
            for idx, probe in enumerate(HIGH_IMPACT_PROBES)
        ]
        interview_set = InterviewSet(questions=fallback_questions[:max_questions])

    typer.echo("Interview probes:")
    for question in interview_set.questions:
        typer.echo(f"- {question.text}")


@app.command("pack")
def pack(
    level: str = typer.Option("L2", "--level", "-l", help="Context depth (L1 or L2)."),
    date: str | None = typer.Option(
        None,
        "--date",
        "-d",
        help="Date (YYYY-MM-DD); auto-detected for L2 when omitted.",
    ),
    output: Path | None = typer.Option(None, "--output", "-o"),
    max_tokens: int | None = typer.Option(None, "--max-tokens"),
    fmt: str = typer.Option("yaml", "--format", help="Output format: yaml or json."),
    history_days: int = typer.Option(
        0,
        "--history-days",
        help="Number of previous days to include (L4 packs only).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show plan without emitting payload."),
) -> None:
    """Assemble a context bundle for prompting."""
    run_pack(
        level,
        date,
        output=output,
        max_tokens=max_tokens,
        fmt=fmt,
        history_days=history_days,
        dry_run=dry_run,
    )


@index_app.command("rebuild")
def index_rebuild(
    since: str | None = typer.Option(
        None,
        "--since",
        help="Earliest date (YYYY-MM-DD or Nd) to include when rebuilding.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Maximum number of normalized files to index (debug/testing).",
    ),
) -> None:
    """Rebuild the Annoy+SQLite retrieval index from normalized YAML."""
    message = run_index_rebuild(since, limit=limit)
    typer.echo(message)


@index_app.command("tail")
def index_tail(
    since: str | None = typer.Option(
        None,
        "--since",
        help="Earliest date (YYYY-MM-DD or Nd) to scan for new normalized files.",
    ),
    days: int = typer.Option(
        7,
        "--days",
        help="Rolling window (days) when --since is omitted.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Maximum number of normalized files to inspect.",
    ),
) -> None:
    """Incrementally ingest new normalized entries into the retrieval index."""
    message = run_index_tail(since, days=days, limit=limit)
    typer.echo(message)


@index_app.command("search")
def index_search(
    query: str = typer.Argument(..., help="Query text to search within indexed chunks."),
    top: int = typer.Option(
        8,
        "--top",
        "-k",
        help="Number of results to display.",
    ),
    tags: str | None = typer.Option(
        None,
        "--tags",
        help="Comma- or space-separated tags to filter by (match any).",
    ),
    source: str | None = typer.Option(
        None,
        "--source",
        help="Comma- or space-separated source types to filter by.",
    ),
    date_from: str | None = typer.Option(
        None,
        "--date-from",
        help="Earliest chunk date (YYYY-MM-DD).",
    ),
    date_to: str | None = typer.Option(
        None,
        "--date-to",
        help="Latest chunk date (YYYY-MM-DD).",
    ),
) -> None:
    """Search the retrieval index and stream formatted results."""
    run_index_search(
        query,
        top=top,
        tags=tags,
        source=source,
        date_from=date_from,
        date_to=date_to,
    )


@app.command("chat")
def chat(
    question: str = typer.Argument(
        ...,
        help="Question to ask your journal assistant.",
    ),
    top: int = typer.Option(
        6,
        "--top",
        "-k",
        help="Maximum number of retrieval chunks to use.",
    ),
    tags: str | None = typer.Option(
        None,
        "--tags",
        help="Optional tag filters (comma or space separated).",
    ),
    source: str | None = typer.Option(
        None,
        "--source",
        help="Optional source-type filters (comma or space separated).",
    ),
    date_from: str | None = typer.Option(
        None,
        "--date-from",
        help="Earliest chunk date (YYYY-MM-DD).",
    ),
    date_to: str | None = typer.Option(
        None,
        "--date-to",
        help="Latest chunk date (YYYY-MM-DD).",
    ),
    session: str | None = typer.Option(
        None,
        "--session",
        help="Session identifier (defaults to chat-YYYYMMDD-HHMMSS).",
    ),
    save: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Persist the turn under derived/chat_sessions/<session>.",
    ),
    feedback: str | None = typer.Option(
        None,
        "--feedback",
        help="Provide 'up' or 'down' to nudge cited claim strengths.",
    ),
) -> None:
    """Run a retrieval-augmented chat turn against your journal."""
    run_chat(
        question,
        top=top,
        tags=tags,
        source=source,
        date_from=date_from,
        date_to=date_to,
        session=session,
        save=save,
        feedback=feedback,
    )


@app.command("chatd")
def chatd(
    host: str = typer.Option("127.0.0.1", "--host", help="Host interface to bind."),
    port: int = typer.Option(8080, "--port", help="Port to listen on."),
) -> None:
    """Start the FastAPI chat daemon (chatd)."""
    run_chatd(host, port)


@app.command("feedback-apply")
def feedback_apply(
    archive: bool = typer.Option(
        True,
        "--archive/--delete",
        help="Archive processed feedback batches (default) or delete them after applying.",
    ),
) -> None:
    """Apply and clear pending chat feedback batches."""

    root = Path.cwd()
    pending_dir = root / "derived" / "pending" / "profile_updates"
    if not pending_dir.exists():
        typer.secho(
            "No pending feedback batches were found (derived/pending/profile_updates missing).",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(1)

    batch_paths = sorted(pending_dir.glob("feedback_*.yaml"))
    if not batch_paths:
        typer.secho("No feedback batches to apply.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)

    claims_path = root / "profile" / "claims.yaml"
    if not claims_path.exists():
        typer.secho(
            "Claims file not found at profile/claims.yaml; run `aijournal profile status` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    claims_file = load_yaml_model(claims_path, ClaimsFile)
    claims_by_id = {claim.id: claim for claim in claims_file.claims}
    total_adjustments: list[tuple[str, float, float]] = []

    archive_dir = pending_dir / "applied_feedback"
    if archive:
        archive_dir.mkdir(parents=True, exist_ok=True)

    for path in batch_paths:
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:  # pragma: no cover - malformed YAML
            typer.secho(
                f"Skipping {path.name}: invalid YAML ({exc}).", fg=typer.colors.RED, err=True
            )
            continue

        adjustments = payload.get("claim_adjustments") or []
        if not isinstance(adjustments, list):
            typer.secho(
                f"Skipping {path.name}: claim_adjustments must be a list.",
                fg=typer.colors.RED,
                err=True,
            )
            continue

        for item in adjustments:
            claim_id = str(item.get("id") or "").strip()
            if not claim_id:
                continue
            target_claim = claims_by_id.get(claim_id)
            if target_claim is None:
                typer.secho(
                    f"{path.name} references unknown claim '{claim_id}' — skipping.",
                    fg=typer.colors.YELLOW,
                    err=True,
                )
                continue
            new_strength = item.get("new_strength")
            try:
                new_value = float(new_strength)
            except (TypeError, ValueError):
                typer.secho(
                    f"Invalid strength '{new_strength}' for '{claim_id}' in {path.name}.",
                    fg=typer.colors.RED,
                    err=True,
                )
                continue
            old_value = float(target_claim.strength)
            clamped_value = max(0.0, min(1.0, new_value))
            target_claim.strength = clamped_value
            total_adjustments.append((claim_id, old_value, clamped_value))

        if archive:
            archive_path = _unique_archive_path(archive_dir / path.name)
            path.rename(archive_path)
        else:
            path.unlink()

    if not total_adjustments:
        typer.secho("No claim adjustments were applied.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)

    write_yaml_model(claims_path, ClaimsFile(claims=list(claims_by_id.values())))

    typer.echo(f"Applied {len(total_adjustments)} feedback adjustment(s):")
    for claim_id, old_value, new_value in total_adjustments:
        delta = new_value - old_value
        sign = "+" if delta >= 0 else ""
        typer.echo(f"- {claim_id}: {old_value:.2f} -> {new_value:.2f} ({sign}{delta:.2f})")


def _unique_archive_path(target: Path) -> Path:
    """Return a unique path by appending a counter when needed."""

    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _coaching_max_questions(profile: dict[str, Any]) -> int:
    prefs = profile.get("coaching_prefs") if isinstance(profile, dict) else {}
    probing = prefs.get("probing") if isinstance(prefs, dict) else None
    max_questions = coerce_int(probing.get("max_questions")) if isinstance(probing, dict) else None
    if max_questions is None or max_questions < 0:
        return 3
    return int(max_questions)


# Ensure Typer command metadata exposes stable names for tests and tooling.
for _command in app.registered_commands:
    if _command.name is None and _command.callback is not None:
        _command.name = _command.callback.__name__.replace("_", "-")

for _group_name in ("profile", "ollama", "index", "persona"):
    if not any(info.name == _group_name for info in app.registered_commands):
        app.registered_commands.append(
            CommandInfo(name=_group_name, callback=lambda _name=_group_name: None, hidden=True)
        )
