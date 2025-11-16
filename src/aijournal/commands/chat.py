"""Chat command orchestration helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from pydantic import BaseModel

from aijournal.commands.index import (
    _format_search_snippet,
    _split_filter_values,
    _validate_date_option,
)
from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.config_loader import load_config, use_fake_llm
from aijournal.common.context import RunContext, create_run_context
from aijournal.io.chat_sessions import ChatSessionRecorder
from aijournal.services.chat import ChatService
from aijournal.services.feedback import (
    FeedbackAdjustment,
    apply_chat_feedback,
    extract_claim_markers,
)
from aijournal.services.retriever import RetrievalFilters
from aijournal.utils import time as time_utils

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aijournal.domain.chat import ChatTurn


class ChatOptions(BaseModel):
    question: str
    top: int
    tags: str | None = None
    source: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    session: str | None = None
    save: bool = False
    feedback: str | None = None


@dataclass(slots=True)
class ChatPrepared:
    question: str
    top: int
    filters: RetrievalFilters
    session_input: str | None
    save: bool
    feedback: str | None


@dataclass(slots=True)
class ChatResult:
    turn: ChatTurn
    prepared: ChatPrepared


def _normalize_feedback_option(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"up", "down"}:
        return normalized
    typer.secho("--feedback must be 'up' or 'down'.", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


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
        (
            "Telemetry: "
            f"retrieval={turn.telemetry.retrieval_ms:.1f}ms "
            f"chunks={turn.telemetry.chunk_count} "
            f"source={turn.telemetry.retriever_source} "
            f"model={turn.telemetry.model}"
        ),
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
    typer.secho("Feedback adjustments applied:", fg=typer.colors.GREEN)
    for adj in adjustments:
        typer.echo(
            f"  claim={adj.claim_id} delta={adj.delta:+.2f} old={adj.old_strength:.2f} new={adj.new_strength:.2f}",
        )
    if feedback_path:
        typer.echo(f"  Saved adjustments to {feedback_path}")
    typer.echo(f"  Feedback: {feedback}")
    typer.echo("")


def prepare_inputs(ctx: RunContext, options: ChatOptions) -> ChatPrepared:
    if options.top <= 0:
        typer.secho("--top must be positive.", fg=typer.colors.RED, err=True)
        ctx.emit(event="command_failed", reason="invalid_top")
        raise typer.Exit(1)

    filters = RetrievalFilters(
        tags=_split_filter_values(options.tags),
        source_types=_split_filter_values(options.source),
        date_from=_validate_date_option(options.date_from, "--date-from"),
        date_to=_validate_date_option(options.date_to, "--date-to"),
    )

    session_input = None
    if isinstance(options.session, str):
        session_input = options.session.strip() or None

    feedback_value = _normalize_feedback_option(options.feedback)

    ctx.emit(
        event="prepare_summary",
        top=options.top,
        save=options.save,
        has_feedback=bool(feedback_value),
    )

    return ChatPrepared(
        question=options.question,
        top=options.top,
        filters=filters,
        session_input=session_input,
        save=options.save,
        feedback=feedback_value,
    )


def invoke_pipeline(ctx: RunContext, prepared: ChatPrepared) -> ChatResult:
    service = ChatService(ctx.workspace, ctx.config)
    try:
        turn = service.run(
            prepared.question,
            top=prepared.top,
            filters=prepared.filters,
        )
    except (RuntimeError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        ctx.emit(event="command_failed", reason="chat_error", error=str(exc))
        raise typer.Exit(1) from exc
    finally:
        service.close()

    ctx.emit(
        event="pipeline_complete",
        intent=turn.intent,
        chunks=turn.telemetry.chunk_count,
    )
    return ChatResult(turn=turn, prepared=prepared)


def persist_output(ctx: RunContext, result: ChatResult) -> None:
    prepared = result.prepared
    turn = result.turn
    session_id = prepared.session_input or time_utils.generate_session_id()
    saved_dir: Path | None = None

    if prepared.save:
        recorder = ChatSessionRecorder(ctx.workspace, session_id)
        recorder.append(turn, feedback=prepared.feedback)
        saved_dir = recorder.session_dir

    _render_chat_turn(
        turn,
        session_id=session_id,
        saved_dir=saved_dir,
        persisted=prepared.save,
    )

    _log_chat_telemetry(turn, session_id=session_id)

    if prepared.feedback:
        adjustments, feedback_path = apply_chat_feedback(
            ctx.workspace,
            turn_answer=turn.answer,
            question=turn.question,
            session_id=session_id,
            timestamp=turn.timestamp,
            feedback=prepared.feedback,
        )
        _render_feedback_summary(adjustments, feedback_path, prepared.feedback)


def run_chat_command(ctx: RunContext, options: ChatOptions) -> None:
    run_command_pipeline(
        ctx,
        options,
        prepare_inputs=prepare_inputs,
        invoke_pipeline=invoke_pipeline,
        persist_output=persist_output,
    )


def run_chat(
    question: str,
    workspace: Path | None = None,
    *,
    top: int,
    tags: str | None,
    source: str | None,
    date_from: str | None,
    date_to: str | None,
    session: str | None,
    save: bool,
    feedback: str | None,
) -> None:
    workspace = workspace or Path.cwd()
    config = load_config(workspace)
    ctx = create_run_context(
        command="chat",
        workspace=workspace,
        config=config,
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )
    options = ChatOptions(
        question=question,
        top=top,
        tags=tags,
        source=source,
        date_from=date_from,
        date_to=date_to,
        session=session,
        save=save,
        feedback=feedback,
    )
    run_chat_command(ctx, options)
