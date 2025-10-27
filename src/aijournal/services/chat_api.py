"""FastAPI application exposing the chat orchestrator as `chatd`."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from aijournal.services.chat import ChatService
from aijournal.services.feedback import FeedbackAdjustment, apply_chat_feedback
from aijournal.services.retriever import RetrievalFilters
from aijournal.utils.coercion import coerce_int

try:  # pragma: no cover - optional dependency
    import orjson
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    orjson = None  # type: ignore[assignment]


class ChatRequest(BaseModel):
    """Incoming chat payload for the API endpoint."""

    question: str = Field(min_length=1)
    top: int | None = Field(default=None, ge=1)
    tags: list[str] | None = None
    source: list[str] | None = None
    date_from: str | None = None
    date_to: str | None = None
    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.\-]+$")
    save: bool = True
    feedback: str | None = None


def _json_line(payload: dict[str, Any]) -> bytes:
    if orjson is not None:
        return orjson.dumps(payload, option=orjson.OPT_APPEND_NEWLINE)
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _validate_date(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        msg = "Date filters must be YYYY-MM-DD"
        raise HTTPException(status_code=400, detail=msg)
    return text


def _as_frozenset(values: Iterable[str] | None) -> frozenset[str]:
    if not values:
        return frozenset()
    cleaned = [value.strip() for value in values if value and value.strip()]
    return frozenset(cleaned)


def _default_session_id() -> str:
    from datetime import UTC, datetime

    return f"chat-{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%S')}"


def build_chat_app(root: Path, config: dict[str, Any] | None = None) -> FastAPI:
    """Return a FastAPI app bound to the chat orchestrator."""

    app = FastAPI(title="aijournal-chatd", version="0.3.0")

    resolved_config = dict(config or {})
    service = ChatService(root, resolved_config)

    # Delay import to avoid circular import during module initialization
    from aijournal.io.chat_sessions import ChatSessionRecorder

    @app.on_event("shutdown")
    def _shutdown() -> None:  # pragma: no cover - FastAPI handles execution
        service.close()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "model": service.model_name(),
        }

    @app.post("/chat")
    def chat_endpoint(payload: ChatRequest) -> StreamingResponse:
        top = coerce_int(payload.top) or 6
        if top <= 0:
            raise HTTPException(status_code=400, detail="--top must be positive")

        filters = RetrievalFilters(
            tags=_as_frozenset(payload.tags),
            source_types=_as_frozenset(payload.source),
            date_from=_validate_date(payload.date_from),
            date_to=_validate_date(payload.date_to),
        )

        feedback_value: str | None = None
        if payload.feedback is not None:
            candidate = payload.feedback.strip().lower()
            if candidate not in {"up", "down"}:
                raise HTTPException(status_code=400, detail="--feedback must be 'up' or 'down'")
            feedback_value = candidate

        try:
            turn = service.run(payload.question, top=top, filters=filters)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        session_id = payload.session_id or _default_session_id()
        saved_dir: Path | None = None
        if payload.save:
            recorder = ChatSessionRecorder(root, session_id)
            recorder.append(turn, feedback=feedback_value)
            saved_dir = recorder.session_dir

        feedback_adjustments: list[FeedbackAdjustment] = []
        feedback_path: Path | None = None
        if feedback_value:
            feedback_adjustments, feedback_path = apply_chat_feedback(
                root,
                turn_answer=turn.answer,
                question=turn.question,
                session_id=session_id,
                timestamp=turn.timestamp,
                feedback=feedback_value,
            )

        def iterator() -> Iterable[bytes]:
            meta = {
                "event": "meta",
                "session_id": session_id,
                "telemetry": {
                    "retrieval_ms": round(turn.telemetry.retrieval_ms, 2),
                    "chunk_count": turn.telemetry.chunk_count,
                    "source": turn.telemetry.retriever_source,
                    "model": turn.telemetry.model,
                },
                "saved_dir": str(saved_dir.relative_to(root)) if saved_dir else None,
                "intent": turn.intent,
                "feedback": feedback_value,
                "feedback_claims": [adj.claim_id for adj in feedback_adjustments],
                "feedback_path": str(feedback_path.relative_to(root)) if feedback_path else None,
            }
            yield _json_line(meta)

            answer = {
                "event": "answer",
                "question": turn.question,
                "answer": turn.answer,
                "citations": [citation.code for citation in turn.citations],
                "clarifying_question": turn.clarifying_question,
                "fake_mode": turn.fake_mode,
            }
            yield _json_line(answer)

        return StreamingResponse(iterator(), media_type="application/x-ndjson")

    return app


__all__ = ["build_chat_app", "ChatRequest"]
