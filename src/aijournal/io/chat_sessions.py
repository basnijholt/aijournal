"""Utilities for persisting chat session transcripts and summaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:  # pragma: no cover - optional dependency
    import orjson
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    orjson = None  # type: ignore[assignment]

from aijournal.services.chat import ChatCitation, ChatTelemetry, ChatTurn


def _dumps_json(payload: dict[str, Any]) -> str:
    if orjson is not None:
        return orjson.dumps(payload, option=orjson.OPT_APPEND_NEWLINE).decode("utf-8")
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _citation_codes(citations: list[ChatCitation]) -> list[str]:
    return [citation.code for citation in citations]


def _telemetry_payload(telemetry: ChatTelemetry) -> dict[str, Any]:
    return {
        "retrieval_ms": round(float(telemetry.retrieval_ms), 2),
        "chunk_count": telemetry.chunk_count,
        "retriever_source": telemetry.retriever_source,
        "model": telemetry.model,
    }


@dataclass
class ChatSessionRecorder:
    """Append chat turns to transcript/summary/learnings artifacts."""

    root: Path
    session_id: str

    def __post_init__(self) -> None:
        self.session_dir = self.root / "derived" / "chat_sessions" / self.session_id.strip()
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._transcript = self.session_dir / "transcript.jsonl"
        self._summary = self.session_dir / "summary.yaml"
        self._learnings = self.session_dir / "learnings.yaml"

    def append(self, turn: ChatTurn, *, feedback: str | None = None) -> None:
        """Record the chat turn across transcript, summary, and learnings files."""
        self._append_transcript(turn, feedback=feedback)
        self._update_summary(turn, feedback=feedback)
        self._update_learnings(turn, feedback=feedback)

    # ------------------------------------------------------------------
    # Transcript persistence
    # ------------------------------------------------------------------
    def _append_transcript(self, turn: ChatTurn, *, feedback: str | None) -> None:
        payloads: list[dict[str, Any]] = [
            {
                "timestamp": turn.timestamp,
                "role": "user",
                "text": turn.question,
                "intent": turn.intent,
            },
            {
                "timestamp": turn.timestamp,
                "role": "assistant",
                "text": turn.answer,
                "intent": turn.intent,
                "citations": _citation_codes(turn.citations),
                "clarifying_question": turn.clarifying_question,
                "telemetry": _telemetry_payload(turn.telemetry),
                "fake_mode": turn.fake_mode,
                "feedback": feedback,
            },
        ]
        with self._transcript.open("a", encoding="utf-8") as handle:
            for record in payloads:
                handle.write(_dumps_json(record))

    # ------------------------------------------------------------------
    # Summary maintenance
    # ------------------------------------------------------------------
    def _update_summary(self, turn: ChatTurn, *, feedback: str | None) -> None:
        summary = _load_yaml(self._summary)
        if not summary:
            summary = {
                "session_id": self.session_id,
                "created_at": turn.timestamp,
                "turn_count": 0,
                "intent_counts": {},
            }

        summary["updated_at"] = turn.timestamp
        summary["turn_count"] = int(summary.get("turn_count", 0) or 0) + 1
        intent_counts = summary.get("intent_counts", {})
        if isinstance(intent_counts, dict):
            intent_counts[turn.intent] = int(intent_counts.get(turn.intent, 0) or 0) + 1
            summary["intent_counts"] = intent_counts
        summary["last_question"] = turn.question
        summary["last_answer_preview"] = turn.answer.split("\n", 1)[0][:160]
        summary["last_citations"] = _citation_codes(turn.citations)
        summary["last_clarifying_question"] = turn.clarifying_question
        summary["last_retrieval_ms"] = round(float(turn.telemetry.retrieval_ms), 2)
        summary["last_feedback"] = feedback
        _write_yaml(self._summary, summary)

    # ------------------------------------------------------------------
    # Learnings rollup (for downstream review)
    # ------------------------------------------------------------------
    def _update_learnings(self, turn: ChatTurn, *, feedback: str | None) -> None:
        payload = _load_yaml(self._learnings)
        if not payload:
            payload = {
                "session_id": self.session_id,
                "created_at": turn.timestamp,
                "learnings": [],
            }
        payload["updated_at"] = turn.timestamp
        learnings = payload.get("learnings")
        if not isinstance(learnings, list):
            learnings = []
        learnings.append(
            {
                "turn_index": len(learnings) + 1,
                "question": turn.question,
                "intent": turn.intent,
                "citations": _citation_codes(turn.citations),
                "clarifying_question": turn.clarifying_question,
                "telemetry": _telemetry_payload(turn.telemetry),
                "feedback": feedback,
            },
        )
        payload["learnings"] = learnings
        _write_yaml(self._learnings, payload)


__all__ = ["ChatSessionRecorder"]
