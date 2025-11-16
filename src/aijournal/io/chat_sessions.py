"""Utilities for persisting chat session transcripts and summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.chat import ChatTelemetry, ChatTurn
from aijournal.domain.chat_sessions import (
    ChatLearningEntry,
    ChatSessionLearnings,
    ChatSessionSummary,
    ChatTranscript,
    ChatTranscriptTurn,
)
from aijournal.io.artifacts import load_artifact, save_artifact

if TYPE_CHECKING:
    from pathlib import Path

    from aijournal.api.chat import ChatCitation


def _citation_codes(citations: list[ChatCitation]) -> list[str]:
    return [citation.code for citation in citations]


@dataclass
class ChatSessionRecorder:
    """Append chat turns to transcript/summary/learnings artifacts."""

    root: Path
    session_id: str

    def __post_init__(self) -> None:
        self.session_dir = self.root / "derived" / "chat_sessions" / self.session_id.strip()
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._transcript = self.session_dir / "transcript.json"
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
        if self._transcript.exists():
            existing = load_artifact(self._transcript, ChatTranscript)
            transcript = existing.data
            meta = existing.meta
        else:
            transcript = ChatTranscript(
                session_id=self.session_id,
                created_at=turn.timestamp,
                updated_at=turn.timestamp,
            )
            meta = ArtifactMeta(created_at=turn.timestamp, model=turn.telemetry.model)

        entry = ChatTranscriptTurn(
            turn_index=len(transcript.turns) + 1,
            timestamp=turn.timestamp,
            question=turn.question,
            answer=turn.answer,
            intent=turn.intent,
            citations=_citation_codes(turn.citations),
            clarifying_question=turn.clarifying_question,
            telemetry=ChatTelemetry(
                retrieval_ms=float(turn.telemetry.retrieval_ms),
                chunk_count=turn.telemetry.chunk_count,
                retriever_source=turn.telemetry.retriever_source,
                model=turn.telemetry.model,
            ),
            feedback=feedback,
            fake_mode=turn.fake_mode,
        )

        transcript.turns.append(entry)
        transcript.updated_at = turn.timestamp
        meta.model = turn.telemetry.model

        save_artifact(
            self._transcript,
            Artifact[ChatTranscript](
                kind=ArtifactKind.CHAT_TRANSCRIPT,
                meta=meta,
                data=transcript,
            ),
            format="json",
        )

    # ------------------------------------------------------------------
    # Summary maintenance
    # ------------------------------------------------------------------
    def _update_summary(self, turn: ChatTurn, *, feedback: str | None) -> None:
        summary: ChatSessionSummary
        meta: ArtifactMeta
        if self._summary.exists():
            try:
                existing = load_artifact(self._summary, ChatSessionSummary)
            except Exception as exc:
                msg = (
                    "Existing chat summary does not match the strict artifact schema. "
                    f"Remove {self._summary} and rerun the command."
                )
                raise RuntimeError(msg) from exc
            summary = existing.data
            meta = existing.meta
        else:
            summary = ChatSessionSummary(
                session_id=self.session_id,
                created_at=turn.timestamp,
                updated_at=turn.timestamp,
            )
            meta = ArtifactMeta(created_at=turn.timestamp, model=turn.telemetry.model)

        summary.turn_count += 1
        summary.updated_at = turn.timestamp

        counts = dict(summary.intent_counts)
        counts[turn.intent] = counts.get(turn.intent, 0) + 1
        summary.intent_counts = counts

        summary.last_question = turn.question
        summary.last_answer_preview = turn.answer.split("\n", 1)[0][:160]
        summary.last_citations = _citation_codes(turn.citations)
        summary.last_clarifying_question = turn.clarifying_question
        summary.last_retrieval_ms = round(float(turn.telemetry.retrieval_ms), 2)
        summary.last_feedback = feedback

        meta.model = turn.telemetry.model

        save_artifact(
            self._summary,
            Artifact[ChatSessionSummary](
                kind=ArtifactKind.CHAT_SUMMARY,
                meta=meta,
                data=summary,
            ),
        )

    # ------------------------------------------------------------------
    # Learnings rollup (for downstream review)
    # ------------------------------------------------------------------
    def _update_learnings(self, turn: ChatTurn, *, feedback: str | None) -> None:
        learnings: ChatSessionLearnings
        meta: ArtifactMeta
        if self._learnings.exists():
            try:
                existing = load_artifact(self._learnings, ChatSessionLearnings)
            except Exception as exc:
                msg = (
                    "Existing chat learnings file does not match the strict artifact schema. "
                    f"Remove {self._learnings} and rerun the command."
                )
                raise RuntimeError(msg) from exc
            learnings = existing.data
            meta = existing.meta
        else:
            learnings = ChatSessionLearnings(
                session_id=self.session_id,
                created_at=turn.timestamp,
                updated_at=turn.timestamp,
            )
            meta = ArtifactMeta(created_at=turn.timestamp, model=turn.telemetry.model)

        entry = ChatLearningEntry(
            turn_index=len(learnings.learnings) + 1,
            question=turn.question,
            intent=turn.intent,
            citations=_citation_codes(turn.citations),
            clarifying_question=turn.clarifying_question,
            telemetry=ChatTelemetry(
                retrieval_ms=float(turn.telemetry.retrieval_ms),
                chunk_count=turn.telemetry.chunk_count,
                retriever_source=turn.telemetry.retriever_source,
                model=turn.telemetry.model,
            ),
            feedback=feedback,
        )

        learnings.learnings.append(entry)
        learnings.updated_at = turn.timestamp
        meta.model = turn.telemetry.model

        save_artifact(
            self._learnings,
            Artifact[ChatSessionLearnings](
                kind=ArtifactKind.CHAT_LEARNINGS,
                meta=meta,
                data=learnings,
            ),
        )
