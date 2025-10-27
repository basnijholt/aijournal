"""Typer CLI entrypoint for aijournal."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, cast

import httpx
import typer
import yaml
from pydantic import ValidationError

from aijournal.commands.facts import (
    _characterization_context,
    _manifest_by_id,
)
from aijournal.commands.facts import (
    run_facts as run_facts_command,
)
from aijournal.commands.ingest import (
    _load_config,
    _load_manifest,
    _load_yaml,
    _manifest_path,
    _parse_datetime,
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
from aijournal.commands.persona import (
    ensure_persona_ready_for_pack,
    persona_state,
    run_persona_build,
)
from aijournal.commands.summarize import (
    _build_meta,
    _entries_to_payload,
    _invoke_structured_llm,
    _json_block,
    _load_normalized_entries,
    _log_entry_progress,
    _structured_call_with_retry,
    _validate_timeout,
)
from aijournal.commands.summarize import (
    run_summarize as run_summarize_command,
)
from aijournal.fakes import (
    fake_profile_suggestions,
)
from aijournal.io.chat_sessions import ChatSessionRecorder
from aijournal.io.yaml_io import load_yaml_model, write_yaml_model
from aijournal.models import (
    AdviceCard,
    AdviceLLMResponse,
    CharacterizeResponse,
    ClaimAtom,
    ClaimConflictPayload,
    ClaimPreviewEvent,
    ClaimProposal,
    ClaimsFile,
    ClaimSignaturePayload,
    ClaimSource,
    FacetProposal,
    InterviewQuestion,
    InterviewSet,
    ManifestEntry,
    NormalizedEntry,
    ProfileSuggestions,
    ProfileSuggestionUpdate,
    ProfileSuggestionUpsert,
    ProfileUpdateBatch,
    ProfileUpdateInput,
    ProfileUpdatePreview,
    ProfileUpdateProposals,
    Provenance,
    Scope,
    SelfProfile,
    SimpleProfileSuggestionsResponse,
    SimpleSuggestion,
)
from aijournal.pipelines import advise as advise_pipeline
from aijournal.pipelines import characterize as characterize_pipeline
from aijournal.pipelines import facts as facts_pipeline
from aijournal.pipelines import index as index_pipeline
from aijournal.pipelines import normalization
from aijournal.pipelines import pack as pack_pipeline
from aijournal.services import (
    ChatService,
    ChatTurn,
    ClaimConflict,
    ClaimConsolidator,
    ClaimMergeOutcome,
    ClaimSignature,
    FeedbackAdjustment,
    LLMResponseError,
    apply_chat_feedback,
    build_chat_app,
    build_ollama_config_from_mapping,
    extract_claim_markers,
    resolve_ollama_host,
)
from aijournal.services.embedding import EmbeddingBackend
from aijournal.services.retriever import RetrievalFilters, Retriever
from aijournal.utils import time as time_utils
from aijournal.utils.coercion import coerce_int
from aijournal.utils.paths import (
    find_data_root,
    normalized_entry_path,
)

app = typer.Typer(help="Local-first personal journal utilities.")
profile_app = typer.Typer(help="Profile utilities.")
ollama_app = typer.Typer(help="Ollama helpers.")
app.add_typer(profile_app, name="profile")
app.add_typer(ollama_app, name="ollama")
index_app = typer.Typer(help="Retrieval index utilities.")
app.add_typer(index_app, name="index")
persona_app = typer.Typer(help="Persona utilities.")
app.add_typer(persona_app, name="persona")


@app.callback()
def main() -> None:
    """Aijournal command-line interface."""
    # Intentionally empty; commands provide functionality.
    return


@dataclass(frozen=True)
class InterviewTarget:
    """Candidate facet/claim/prompt ranked for interview follow-ups."""

    path: str
    score: float
    kind: Literal["facet", "claim", "pending"]
    reasons: tuple[str, ...] = ()
    claim_id: str | None = None
    missing_context: tuple[str, ...] = ()


INDEX_DB_FILENAME = "index.db"
ANNOY_FILENAME = "annoy.index"
INDEX_META_FILENAME = "meta.json"
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


DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_LLM_RETRIES = 1


def _normalize_created_at(value: Any) -> str:
    return normalization.normalize_created_at(value)


def _coerce_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value.astimezone(UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(value)
    return text if text else None


def _default_claim_sources(raw: ClaimAtom | dict[str, Any]) -> list[ClaimSource]:
    claim_id: str | None
    if isinstance(raw, ClaimAtom):
        claim_id = raw.id
    elif isinstance(raw, dict):
        claim_id_raw = raw.get("id")
        claim_id = str(claim_id_raw) if claim_id_raw else None
    else:
        claim_id = None
    if not claim_id:
        return []
    claim_id_str = str(claim_id)
    return [ClaimSource(entry_id=claim_id_str, spans=[])]


def _normalize_claim_atom(
    data: ClaimAtom | dict[str, Any],
    *,
    timestamp: str,
    default_sources: Sequence[ClaimSource] | None = None,
) -> ClaimAtom:
    if default_sources is None:
        default_sources = _default_claim_sources(data)
    return normalization.normalize_claim_atom(
        data,
        timestamp=timestamp,
        default_sources=default_sources,
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
    return _normalize_claim_atom(raw, timestamp=timestamp, default_sources=default_sources)


def _load_normalized_entries_with_paths(root: Path, day: str) -> list[tuple[NormalizedEntry, Path]]:
    folder = root / "data" / "normalized" / day
    if not folder.exists():
        return []
    entries: list[tuple[NormalizedEntry, Path]] = []
    for file in sorted(folder.glob("*.yaml")):
        entries.append((load_yaml_model(file, NormalizedEntry), file))
    return entries


def _derived_advice_path(root: Path, day: str, question: str) -> Path:
    slug = time_utils.slugify_title(question)
    return root / "derived" / "advice" / day / f"{slug}.yaml"


def _derived_profile_suggestions_path(root: Path, day: str) -> Path:
    return root / "derived" / "profile_suggestions" / f"{day}.yaml"


def _pending_updates_dir(root: Path) -> Path:
    return root / PENDING_UPDATES_SUBDIR


def _pending_updates_path(root: Path, batch_id: str) -> Path:
    safe_id = batch_id.replace(":", "-")
    return _pending_updates_dir(root) / f"{safe_id}.yaml"


def _latest_pending_batch(root: Path) -> Path | None:
    directory = _pending_updates_dir(root)
    if not directory.exists():
        return None
    files = sorted(p for p in directory.glob("*.yaml") if p.is_file())
    return files[-1] if files else None


def _collect_pending_interview_prompts(root: Path, limit: int = 5) -> list[str]:
    directory = _pending_updates_dir(root)
    if not directory.exists():
        return []
    prompts: list[str] = []
    for path in sorted((p for p in directory.glob("*.yaml") if p.is_file()), reverse=True):
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        preview = payload.get("preview") or {}
        for prompt in preview.get("interview_prompts") or []:
            text = str(prompt).strip()
            if text and text not in prompts:
                prompts.append(text)
        if len(prompts) >= limit:
            break
    return prompts[:limit]


def _profile_suggestions_payload(
    entries: Sequence[NormalizedEntry],
    profile: dict[str, Any],
    claims: Sequence[ClaimAtom],
    date: str,
    config: dict[str, Any],
    *,
    timeout: float | None = None,
    retries: int = DEFAULT_LLM_RETRIES,
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
            _structured_call_with_retry(
                lambda: _invoke_structured_llm(
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
                ),
                retries=retries,
                label=f"profile suggest {date}",
            ),
        )
        timestamp = time_utils.format_timestamp(time_utils.now())
        suggestions = _simple_suggestions_to_profile(simple_response, timestamp=timestamp)

    suggestions.meta = _build_meta("prompts/profile_suggest.md", config=config)
    return suggestions


def _normalize_claim_proposals(
    raw_claims: Iterable[Any],
    *,
    normalized_ids: list[str],
    evidence_hashes: list[str],
    manifest_hashes: list[str],
    default_sources: Sequence[ClaimSource],
    timestamp: str,
) -> list[ClaimProposal]:
    return facts_pipeline.normalize_claim_proposals(
        raw_claims,
        normalized_ids=normalized_ids,
        evidence_hashes=evidence_hashes,
        manifest_hashes=manifest_hashes,
        default_sources=default_sources,
        timestamp=timestamp,
    )


def _characterize_payload(
    date: str,
    entries: Sequence[NormalizedEntry],
    profile: dict[str, Any],
    claims: Sequence[ClaimAtom],
    manifest_index: dict[str, ManifestEntry],
    config: dict[str, Any],
    *,
    timeout: float | None = None,
    retries: int = DEFAULT_LLM_RETRIES,
) -> tuple[ProfileUpdateProposals, list[str]]:
    claim_timestamp = time_utils.format_timestamp(time_utils.now())
    context = _characterization_context(entries, manifest_index)
    target_date = date or time_utils.created_date(claim_timestamp)
    manifest_payload = _json_block(
        {key: entry.model_dump(mode="python") for key, entry in manifest_index.items()},
    )

    def request_characterize() -> CharacterizeResponse:
        return cast(
            CharacterizeResponse,
            _invoke_structured_llm(
                "prompts/characterize.md",
                {
                    "date": target_date,
                    "entries_json": _json_block(_entries_to_payload(entries)),
                    "profile_json": _json_block(profile),
                    "claims_json": _json_block(
                        {"claims": [claim.model_dump(mode="python") for claim in claims]}
                    ),
                    "manifest_json": manifest_payload,
                },
                response_model=CharacterizeResponse,
                agent_name="aijournal-characterize",
                config=config,
                timeout=timeout,
            ),
        )

    proposals, prompts = characterize_pipeline.generate_characterization(
        entries,
        profile,
        claims,
        use_fake_llm=_use_fake_llm(),
        structured_call=_structured_call_with_retry,
        request_factory=request_characterize,
        retries=retries,
        label=f"characterize {target_date}",
        context=context,
        claim_timestamp=claim_timestamp,
        build_claim=_build_claim_atom_from_entry,
        normalize_claims=_normalize_claim_proposals,
        normalize_facets=characterize_pipeline.normalize_facet_proposals,
    )
    return proposals, prompts


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

    def request_advice() -> AdviceLLMResponse:
        return cast(
            AdviceLLMResponse,
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
                response_model=AdviceLLMResponse,
                agent_name="aijournal-advise",
                config=config,
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
    except LLMResponseError as exc:
        typer.secho(f"Profile suggestions failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    path = _derived_profile_suggestions_path(root, date)
    write_yaml_model(path, suggestions_model)
    typer.echo(str(path))


@profile_app.command("apply")
def profile_apply(
    date: str = typer.Option(..., "--date", "-d", help="Date (YYYY-MM-DD) to apply."),
    file: Path | None = typer.Option(None, "--file", help="Path to suggestions YAML."),
    yes: bool = typer.Option(False, "--yes", help="Apply without prompting."),
) -> None:
    """Apply profile suggestions to authoritative files (offline)."""
    root = Path.cwd()
    suggestions_path = file or (root / "derived" / "profile_suggestions" / f"{date}.yaml")

    if not suggestions_path.exists():
        typer.secho(
            f"Suggestions file not found: {suggestions_path}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    suggestions_model = load_yaml_model(suggestions_path, ProfileSuggestions)
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
    typer.echo("Applied 1 suggestions file")


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
    root = Path.cwd()
    entries_with_paths = _load_normalized_entries_with_paths(root, date)
    if not entries_with_paths:
        typer.secho(f"No normalized entries for {date}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    timeout_value = _validate_timeout(timeout)
    manifest_entries = _load_manifest(_manifest_path(root))
    manifest_index = _manifest_by_id(manifest_entries)
    profile_model, claim_models = _load_profile_components(root)
    profile = _profile_to_dict(profile_model)
    config = _load_config(root)

    entries = [entry for entry, _ in entries_with_paths]
    _log_entry_progress(f"Characterizing entries for {date}", entries, progress)
    try:
        proposals_model, interview_prompts = _characterize_payload(
            date,
            entries,
            profile,
            claim_models,
            manifest_index,
            config,
            timeout=timeout_value,
            retries=retries,
        )
    except LLMResponseError as exc:
        typer.secho(f"Characterize failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    timestamp = time_utils.format_timestamp(time_utils.now())
    batch_id = f"{date}-{timestamp}"

    preview_model = _build_claim_preview(
        proposals_model.claims,
        claim_models,
        timestamp=timestamp,
    )
    if interview_prompts:
        prompts = [prompt for prompt in interview_prompts if prompt]
        if prompts:
            if preview_model is None:
                preview_model = ProfileUpdatePreview(
                    interview_prompts=facts_pipeline.merge_unique([], prompts)
                )
            else:
                preview_model.interview_prompts = facts_pipeline.merge_unique(
                    preview_model.interview_prompts,
                    prompts,
                )

    inputs: list[ProfileUpdateInput] = []
    for data, path in entries_with_paths:
        entry_id = data.id or path.stem
        manifest_entry = manifest_index.get(entry_id)
        manifest_hash = manifest_entry.hash if manifest_entry else None
        inputs.append(
            ProfileUpdateInput(
                id=entry_id,
                normalized_path=_relative_source_path(path, root),
                source_hash=data.source_hash or manifest_hash,
                manifest_hash=manifest_hash,
                tags=list(data.tags or []),
            ),
        )
    meta_model = _build_meta("prompts/characterize.md", config=config)
    batch_model = ProfileUpdateBatch(
        batch_id=batch_id,
        created_at=timestamp,
        date=date,
        inputs=inputs,
        proposals=proposals_model,
        meta=meta_model,
        preview=preview_model,
    )
    pending_dir = _pending_updates_dir(root)
    pending_dir.mkdir(parents=True, exist_ok=True)
    batch_path = _pending_updates_path(root, batch_id)
    write_yaml_model(batch_path, batch_model)
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
    root = Path.cwd()
    profile_model, claim_models = _load_profile_components(root)
    profile = _profile_to_dict(profile_model)
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
    advice_card.meta = _build_meta("prompts/advise.md", model=model_name)

    day = time_utils.created_date(time_utils.format_timestamp(time_utils.now()))
    advice_path = _derived_advice_path(root, day, question)
    write_yaml_model(advice_path, advice_card)
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


def _days_between(now: datetime, past: str | None) -> float | None:
    if not past:
        return None
    dt = _parse_datetime(past)
    if not dt:
        return None
    delta = now - dt
    return delta.total_seconds() / 86400.0


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


def _claims_to_models(claims: Sequence[Any]) -> list[ClaimAtom]:
    normalized: list[ClaimAtom] = []
    timestamp = time_utils.format_timestamp(time_utils.now())
    for raw in claims:
        if not isinstance(raw, (dict, ClaimAtom)):
            continue
        try:
            normalized.append(
                _normalize_claim_atom(
                    raw,
                    timestamp=timestamp,
                ),
            )
        except (ValidationError, ValueError):
            continue
    return normalized


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


def _apply_claim_upsert(
    claims: list[ClaimAtom],
    value: ClaimAtom | dict[str, Any],
    timestamp: str,
    events: list[ClaimMergeOutcome] | None = None,
) -> bool:
    try:
        normalized = _normalize_claim_atom(value, timestamp=timestamp)
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
                incoming = _normalize_claim_atom(raw_claim, timestamp=timestamp)
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


def _claim_last_updated(claim: ClaimAtom) -> str | None:
    return _coerce_timestamp(claim.provenance.last_updated)


def _profile_status_impl() -> None:
    root = Path.cwd()
    profile_model, claim_models = _load_profile_components(root)
    profile = _profile_to_dict(profile_model)
    config_path = root / "config" / "config.yaml"
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


@profile_app.command("status")
def profile_status() -> None:
    """Show ranked facets/claims needing review."""
    _profile_status_impl()


@app.command("profile-status")
def profile_status_alias() -> None:
    """Alias command for profile status (for backwards compatibility)."""
    _profile_status_impl()


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


def _write_json_if_changed(path: Path, payload: dict[str, Any]) -> bool:
    existing = None
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = None
    if existing == payload:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    return True


def _latest_normalized_day(root: Path) -> str | None:
    base = root / "data" / "normalized"
    if not base.exists():
        return None
    candidates = sorted(p.name for p in base.iterdir() if p.is_dir())
    return candidates[-1] if candidates else None


def _resolve_pack_date(level: str, requested: str | None, root: Path) -> str:
    if requested:
        return requested
    if level == "L1":
        return time_utils.now().strftime("%Y-%m-%d")
    latest = _latest_normalized_day(root)
    if latest:
        return latest
    typer.secho("No normalized entries available; provide --date.", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


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
    level = level.upper()
    fmt_value = fmt.lower()
    if fmt_value not in {"yaml", "json"}:
        typer.secho(f"Unsupported format: {fmt}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    fmt = fmt_value
    if history_days < 0:
        typer.secho("--history-days must be zero or positive.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if level != "L4" and history_days:
        typer.secho("--history-days is only supported for L4 packs.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    default_budget = {"L1": 1200, "L2": 2000, "L3": 2600, "L4": 3200}
    budget = max_tokens or default_budget.get(level, 2000)

    root = Path.cwd()
    config = _load_config(root)
    _, _, char_per_token = _index_settings(config)
    ensure_persona_ready_for_pack(root)
    resolved_date = _resolve_pack_date(level, date, root)
    try:
        entries_info = pack_pipeline.collect_pack_entries(
            root,
            level,
            resolved_date,
            history_days if level == "L4" else 0,
        )
    except pack_pipeline.PackAssemblyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    entries_payload: list[pack_pipeline.PackEntry] = []
    for role, path in entries_info:
        text = path.read_text(encoding="utf-8")
        rel = _relative_source_path(path, root)
        tokens = index_pipeline.token_estimate(text, char_per_token)
        entries_payload.append(
            pack_pipeline.PackEntry(
                role=role,
                path=rel,
                tokens=tokens,
                content=text,
            ),
        )

    total_tokens = sum(entry.tokens for entry in entries_payload)
    trimmed: list[pack_pipeline.TrimmedFile] = []
    if total_tokens > budget:
        pack_pipeline.trim_entries(entries_payload, budget, trimmed)
        total_tokens = sum(entry.tokens for entry in entries_payload)

    payload = pack_pipeline.build_pack_payload(
        entries_payload,
        level,
        resolved_date,
        trimmed,
        total_tokens,
        budget,
    )

    _log_pack_metrics(
        level,
        total_tokens,
        budget,
        len(trimmed),
        dry_run=dry_run,
        output=output,
    )

    if dry_run:
        typer.echo("Planned files:")
        for entry in entries_payload:
            typer.echo(f"- {entry.path} ({entry.tokens} tokens)")
        if trimmed:
            trimmed_display = ", ".join(f"{item.role}:{item.path}" for item in trimmed)
            typer.echo(f"trimmed: {trimmed_display}")
        return

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        changed = False
        if fmt == "json":
            changed = _write_json_if_changed(output, payload.to_dict())
        else:
            changed = _write_yaml_if_changed(output, payload.to_dict())
        if changed:
            typer.echo(str(output))
        else:
            typer.echo("No changes")
        return

    if fmt == "json":
        typer.echo(json.dumps(payload.to_dict(), indent=2))
    else:
        typer.echo(yaml.safe_dump(payload.to_dict(), sort_keys=False))


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
    root = Path.cwd()
    if limit is not None and limit <= 0:
        typer.secho("--limit must be positive when provided.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    since_filter = _resolve_since_filter(since)
    entries = _collect_normalized_files(root, since_filter)
    if limit is not None:
        entries = entries[:limit]
    if not entries:
        typer.secho(
            "No normalized entries available for indexing.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    manifest_index = _manifest_by_id(_load_manifest(_manifest_path(root)))
    tasks = index_pipeline.prepare_index_tasks(
        entries,
        root=root,
        manifest_index=manifest_index,
        relative_path=lambda entry_path: _relative_source_path(entry_path, root),
    )
    if not tasks:
        typer.secho("No normalized entries with valid IDs found.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    config = _load_config(root)
    embedder = _build_embedding_backend(config)
    ann_trees, search_k_factor, char_per_token = _index_settings(config)

    db_path = _index_db_path(root)
    index_dir = _index_dir(root)
    index_dir.mkdir(parents=True, exist_ok=True)
    conn = _connect_index_db(db_path, overwrite=True)
    with conn:
        _prepare_index_schema(conn)
        stats = index_pipeline.index_entries(conn, tasks, embedder, char_per_token)

    chunk_total, entry_total = index_pipeline.gather_index_stats(conn)
    index_pipeline.rebuild_annoy_index(conn, embedder.dim, ann_trees, _annoy_index_path(root))
    conn.commit()
    if stats["dates"]:
        index_pipeline.write_chunk_manifests(
            conn,
            _chunk_manifest_dir(root),
            stats["dates"],
            embedder,
        )
    conn.close()

    index_pipeline.write_index_meta(
        root,
        embedder=embedder,
        chunk_total=chunk_total,
        entry_total=entry_total,
        mode="rebuild",
        fake_mode=_use_fake_llm(),
        ann_trees=ann_trees,
        search_k_factor=search_k_factor,
        char_per_token=char_per_token,
        since=since_filter,
        limit=limit,
        touched_dates=stats["dates"],
        index_meta_path=_index_meta_path,
    )

    typer.echo(
        f"Indexed {chunk_total} chunks across {entry_total} entries (mode: rebuild).",
    )


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
    if days <= 0:
        typer.secho("--days must be positive.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if limit is not None and limit <= 0:
        typer.secho("--limit must be positive when provided.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    root = Path.cwd()
    db_path = _index_db_path(root)
    if not db_path.exists():
        typer.secho(
            "Index database not found. Run `aijournal index rebuild` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    since_filter = _resolve_since_filter(since, fallback_days=days)
    entries = _collect_normalized_files(root, since_filter)
    if limit is not None:
        entries = entries[:limit]
    if not entries:
        typer.secho(
            "No normalized entries matched the requested window.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    manifest_index = _manifest_by_id(_load_manifest(_manifest_path(root)))
    tasks = index_pipeline.prepare_index_tasks(
        entries,
        root=root,
        manifest_index=manifest_index,
        relative_path=lambda entry_path: _relative_source_path(entry_path, root),
    )
    conn = _connect_index_db(db_path)
    try:
        pending = index_pipeline.filter_tasks_for_tail(conn, tasks)
        if not pending:
            typer.echo("Index already up to date for requested window.")
            return

        config = _load_config(root)
        embedder = _build_embedding_backend(config)
        ann_trees, search_k_factor, char_per_token = _index_settings(config)

        with conn:
            _prepare_index_schema(conn)
            stats = index_pipeline.index_entries(conn, pending, embedder, char_per_token)

        chunk_total, entry_total = index_pipeline.gather_index_stats(conn)
        index_pipeline.rebuild_annoy_index(conn, embedder.dim, ann_trees, _annoy_index_path(root))
        conn.commit()
        if stats["dates"]:
            index_pipeline.write_chunk_manifests(
                conn,
                _chunk_manifest_dir(root),
                stats["dates"],
                embedder,
            )

        index_pipeline.write_index_meta(
            root,
            embedder=embedder,
            chunk_total=chunk_total,
            entry_total=entry_total,
            mode="tail",
            fake_mode=_use_fake_llm(),
            ann_trees=ann_trees,
            search_k_factor=search_k_factor,
            char_per_token=char_per_token,
            since=since_filter,
            limit=limit,
            touched_dates=stats["dates"],
            index_meta_path=_index_meta_path,
        )

        typer.echo(
            f"Indexed {stats['chunks']} chunks across {stats['entries']} entries (mode: tail).",
        )
    finally:
        conn.close()


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
    if top <= 0:
        typer.secho("--top must be positive.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    root = Path.cwd()
    config = _load_config(root)
    filters = RetrievalFilters(
        tags=_split_filter_values(tags),
        source_types=_split_filter_values(source),
        date_from=_validate_date_option(date_from, "--date-from"),
        date_to=_validate_date_option(date_to, "--date-to"),
    )

    retriever = Retriever(root, config)
    try:
        result = retriever.search(query, k=top, filters=filters)
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    finally:
        retriever.close()

    if not result.chunks:
        typer.echo("No matches found.")
        return

    header = f"Top {len(result.chunks)} match(es) - source: {result.meta.source}"
    if result.meta.fake_mode:
        header += " (fake mode)"
    typer.echo(header)

    for idx, chunk in enumerate(result.chunks, start=1):
        tag_display = ", ".join(chunk.tags) if chunk.tags else "-"
        source_path = chunk.source_path or chunk.normalized_id
        snippet = _format_search_snippet(chunk.text)
        typer.echo(
            f"{idx}. [{chunk.date}] {source_path}",
        )
        typer.echo(
            f"   score: {chunk.score:.3f}  tags: {tag_display}",
        )
        typer.echo(f"   {snippet}")
        if idx != len(result.chunks):
            typer.echo("")


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
    if top <= 0:
        typer.secho("--top must be positive.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    root = Path.cwd()
    config = _load_config(root)
    filters = RetrievalFilters(
        tags=_split_filter_values(tags),
        source_types=_split_filter_values(source),
        date_from=_validate_date_option(date_from, "--date-from"),
        date_to=_validate_date_option(date_to, "--date-to"),
    )

    service = ChatService(root, config)
    try:
        turn = service.run(question, top=top, filters=filters)
    except (RuntimeError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    finally:
        service.close()

    feedback_value = _normalize_feedback_option(feedback)
    session_id = session.strip() if isinstance(session, str) and session.strip() else None
    saved_dir: Path | None = None
    if save:
        session_id = session_id or time_utils.generate_session_id()
        recorder = ChatSessionRecorder(root, session_id)
        recorder.append(turn, feedback=feedback_value)
        saved_dir = recorder.session_dir
    else:
        session_id = session_id or time_utils.generate_session_id()

    _render_chat_turn(
        turn,
        session_id=session_id,
        saved_dir=saved_dir,
        persisted=save,
    )

    _log_chat_telemetry(turn, session_id=session_id)

    if feedback_value:
        adjustments, feedback_path = apply_chat_feedback(
            root,
            turn_answer=turn.answer,
            question=turn.question,
            session_id=session_id,
            timestamp=turn.timestamp,
            feedback=feedback_value,
        )
        _render_feedback_summary(adjustments, feedback_path, feedback_value)


@app.command("chatd")
def chatd(
    host: str = typer.Option("127.0.0.1", "--host", help="Host interface to bind."),
    port: int = typer.Option(8080, "--port", help="Port to listen on."),
) -> None:
    """Start the FastAPI chat daemon (chatd)."""
    if port <= 0 or port > 65535:
        typer.secho("--port must be between 1 and 65535.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - depends on optional deps
        typer.secho(
            f"uvicorn is required for chatd: {exc}. Install with `uv add uvicorn fastapi`.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    root = Path.cwd()
    config = _load_config(root)
    app_instance = build_chat_app(root, config)
    typer.echo(f"chatd starting on http://{host}:{port}")
    uvicorn.run(app_instance, host=host, port=port, log_level="info")


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


def _index_dir(root: Path) -> Path:
    return root / "derived" / "index"


def _index_db_path(root: Path) -> Path:
    return _index_dir(root) / INDEX_DB_FILENAME


def _annoy_index_path(root: Path) -> Path:
    return _index_dir(root) / ANNOY_FILENAME


def _chunk_manifest_dir(root: Path) -> Path:
    return _index_dir(root) / "chunks"


def _index_meta_path(root: Path) -> Path:
    return _index_dir(root) / INDEX_META_FILENAME


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


def _collect_normalized_files(
    root: Path,
    since: str | None,
) -> list[tuple[str, Path]]:
    normalized_root = root / "data" / "normalized"
    if not normalized_root.exists():
        return []
    entries: list[tuple[str, Path]] = []
    for day_dir in sorted(p for p in normalized_root.iterdir() if p.is_dir()):
        day = day_dir.name
        if since and day < since:
            continue
        for file in sorted(day_dir.glob("*.yaml")):
            entries.append((day, file))
    return entries


def _resolve_since_filter(value: str | None, fallback_days: int | None = None) -> str | None:
    if value:
        text = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
        if text.endswith("d") and text[:-1].isdigit():
            window = int(text[:-1])
            return (time_utils.now() - timedelta(days=window)).strftime("%Y-%m-%d")
        typer.secho(
            "--since must be YYYY-MM-DD or Nd (e.g., 7d)",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    if fallback_days is not None:
        return (time_utils.now() - timedelta(days=fallback_days)).strftime("%Y-%m-%d")
    return None


def _validate_date_option(value: str | None, option: str) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        typer.secho(f"{option} must be YYYY-MM-DD.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    return text


def _split_filter_values(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    parts = [part.strip() for part in re.split(r"[,\s]+", raw) if part.strip()]
    return frozenset(parts)


def _normalize_feedback_option(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"up", "down"}:
        return normalized
    typer.secho("--feedback must be 'up' or 'down'.", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _coaching_max_questions(profile: dict[str, Any]) -> int:
    prefs = profile.get("coaching_prefs") if isinstance(profile, dict) else {}
    probing = prefs.get("probing") if isinstance(prefs, dict) else None
    max_questions = coerce_int(probing.get("max_questions")) if isinstance(probing, dict) else None
    if max_questions is None or max_questions < 0:
        return 3
    return int(max_questions)


def _render_chat_turn(
    turn: ChatTurn,
    *,
    session_id: str | None,
    saved_dir: Path | None,
    persisted: bool,
) -> None:
    mode_label = "fake mode" if turn.fake_mode else "live mode"
    typer.echo(f"Chat response ({mode_label})")
    if session_id:
        typer.echo(f"Session: {session_id}")
    typer.echo(f"Question: {turn.question}")
    typer.echo(f"Intent: {turn.intent}")
    typer.echo("Answer:")
    answer_lines = turn.answer.splitlines() or [turn.answer]
    for line in answer_lines:
        typer.echo(f"  {line}")

    if turn.clarifying_question:
        typer.echo("")
        typer.echo(f"Clarifying question: {turn.clarifying_question}")

    typer.echo("")
    typer.echo(
        f"Telemetry: retrieval={turn.telemetry.retrieval_ms:.1f}ms chunks={turn.telemetry.chunk_count} source={turn.telemetry.retriever_source} model={turn.telemetry.model}",
    )

    if not turn.citations:
        if turn.retrieved_chunks:
            typer.echo("")
            typer.echo("Citations: none referenced.")
        else:
            typer.echo("")
            typer.echo("No journal chunks were retrieved.")
    else:
        typer.echo("")
        typer.echo("Citations:")
        chunk_map = {chunk.chunk_id: chunk for chunk in turn.retrieved_chunks}
        for idx, citation in enumerate(turn.citations, start=1):
            chunk = chunk_map.get(citation.chunk_id)
            source_path = citation.source_path or citation.normalized_id
            typer.echo(
                f"{idx}. {citation.marker} {source_path} ({citation.date}) score {citation.score:.3f}",
            )
            if chunk:
                snippet = _format_search_snippet(chunk.text)
                tag_display = ", ".join(citation.tags) if citation.tags else "-"
                typer.echo(f"   tags: {tag_display}")
                typer.echo(f"   {snippet}")
            if idx != len(turn.citations):
                typer.echo("")

    if persisted and saved_dir is not None:
        typer.echo("")
        typer.echo(f"Saved transcript: {saved_dir}")


def _log_chat_telemetry(turn: ChatTurn, *, session_id: str | None) -> None:
    claim_markers = extract_claim_markers(turn.answer)
    payload = {
        "event": "chat.telemetry",
        "session_id": session_id,
        "intent": turn.intent,
        "retrieval_ms": round(turn.telemetry.retrieval_ms, 2),
        "chunks": turn.telemetry.chunk_count,
        "model": turn.telemetry.model,
        "clarifying": bool(turn.clarifying_question),
        "claim_markers": claim_markers,
    }
    if not claim_markers and session_id is not None and turn.persona.claims:
        typer.secho(
            "No persona claim markers were referenced; thumbs up/down cannot adjust claim strengths.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    typer.echo(json.dumps(payload, ensure_ascii=False), err=True)


def _render_feedback_summary(
    adjustments: Sequence[FeedbackAdjustment],
    feedback_path: Path | None,
    feedback: str,
) -> None:
    if not adjustments:
        typer.secho(
            "Feedback provided but no claim citations were found to adjust.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return

    typer.echo("")
    typer.echo(f"Recorded {feedback} feedback for {len(adjustments)} claim(s):")
    for adj in adjustments:
        sign = "+" if adj.delta >= 0 else ""
        typer.echo(
            f"- {adj.claim_id}: {adj.old_strength:.2f} -> {adj.new_strength:.2f} ({sign}{adj.delta:.2f})",
        )
    if feedback_path is not None:
        typer.echo(f"Queued feedback batch: {feedback_path}")


def _log_pack_metrics(
    level: str,
    total_tokens: int,
    budget: int,
    trimmed_count: int,
    *,
    dry_run: bool,
    output: Path | None,
) -> None:
    payload = {
        "event": "pack.telemetry",
        "level": level,
        "total_tokens": total_tokens,
        "budget": budget,
        "trimmed": trimmed_count,
        "dry_run": dry_run,
        "output": str(output) if output else None,
    }
    typer.echo(json.dumps(payload, ensure_ascii=False), err=True)


def _format_search_snippet(text: str, limit: int = 200) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."


def _build_embedding_backend(config: dict[str, Any]) -> EmbeddingBackend:
    model = str(config.get("embedding_model") or "nomic-embed-text")
    host = os.getenv("AIJOURNAL_OLLAMA_HOST")
    return EmbeddingBackend(model, host=host, fake_mode=_use_fake_llm())


def _index_settings(config: dict[str, Any]) -> tuple[int, float, float]:
    index_cfg_raw = config.get("index")
    index_cfg = index_cfg_raw if isinstance(index_cfg_raw, dict) else {}
    ann_trees = int(index_cfg.get("ann_trees") or 50)
    search_k_factor = float(index_cfg.get("search_k_factor") or 3.0)
    token_cfg_raw = config.get("token_estimator")
    token_cfg = token_cfg_raw if isinstance(token_cfg_raw, dict) else {}
    char_per_token = float(token_cfg.get("char_per_token") or 4.2)
    return ann_trees, search_k_factor, char_per_token


def _connect_index_db(path: Path, *, overwrite: bool = False) -> sqlite3.Connection:
    if overwrite and path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _prepare_index_schema(conn: sqlite3.Connection) -> None:
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                normalized_id TEXT NOT NULL,
                normalized_path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                chunk_text TEXT NOT NULL,
                date TEXT NOT NULL,
                tags TEXT NOT NULL,
                source_type TEXT,
                source_path TEXT,
                tokens INTEGER NOT NULL,
                source_hash TEXT,
                manifest_hash TEXT,
                embedding BLOB NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts
            USING fts5(
                chunk_id UNINDEXED,
                chunk_text,
                content=''
            );
            CREATE TABLE IF NOT EXISTS sources (
                normalized_path TEXT PRIMARY KEY,
                normalized_id TEXT NOT NULL,
                date TEXT NOT NULL,
                source_hash TEXT,
                manifest_hash TEXT,
                chunk_count INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS annoy_map (
                annoy_idx INTEGER PRIMARY KEY,
                chunk_id TEXT NOT NULL
            );
            """,
        )
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "fts5" in message:
            msg = (
                "SQLite runtime does not support FTS5, which is required for the retrieval index. "
                "Install a Python build with FTS5 enabled (e.g., the system sqlite3 on macOS via Homebrew) "
                "or rebuild Python against an FTS5-capable SQLite."
            )
            raise RuntimeError(msg) from exc
        raise
