"""Chat orchestration service built on top of Retriever and persona core."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from aijournal.io.yaml_io import load_yaml_model
from aijournal.models import PersonaCore, PersonaCoreFile
from aijournal.services.ollama import (
    LLMResponseError,
    OllamaConfig,
    build_ollama_config_from_mapping,
    run_ollama_agent,
)
from aijournal.services.retriever import RetrievalFilters, RetrievedChunk, Retriever
from aijournal.utils.coercion import coerce_float, coerce_int


@dataclass(frozen=True)
class ChatCitation:
    """Reference to a retrieved chunk included in a chat response."""

    chunk_id: str
    code: str
    normalized_id: str
    chunk_index: int
    source_path: str
    date: str
    tags: tuple[str, ...]
    score: float

    @property
    def marker(self) -> str:
        """Return the display marker used inside responses."""
        return f"[entry:{self.code}]"

    @classmethod
    def from_chunk(cls, chunk: RetrievedChunk) -> ChatCitation:
        code = f"{chunk.normalized_id}#p{chunk.chunk_index}"
        return cls(
            chunk_id=chunk.chunk_id,
            code=code,
            normalized_id=chunk.normalized_id,
            chunk_index=chunk.chunk_index,
            source_path=chunk.source_path,
            date=chunk.date,
            tags=tuple(chunk.tags),
            score=chunk.score,
        )


@dataclass(frozen=True)
class ChatTurn:
    """Result of a single chat turn."""

    question: str
    answer: str
    persona: PersonaCore
    citations: list[ChatCitation]
    retrieved_chunks: list[RetrievedChunk]
    fake_mode: bool


class ChatService:
    """Minimal chat orchestrator that composes persona + retrieval."""

    def __init__(
        self,
        root: Path,
        config: dict[str, Any] | None = None,
        *,
        retriever: Retriever | None = None,
    ) -> None:
        self._root = Path(root)
        self._config = dict(config or {})
        self._persona_path = self._root / "derived" / "persona" / "persona_core.yaml"
        self._fake_mode = os.getenv("AIJOURNAL_FAKE_OLLAMA") == "1"
        self._retriever = retriever or Retriever(self._root, self._config)

        chat_cfg_raw = self._config.get("chat")
        self._chat_cfg = chat_cfg_raw if isinstance(chat_cfg_raw, dict) else {}

    def close(self) -> None:
        """Release underlying resources."""
        self._retriever.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(
        self,
        question: str,
        *,
        top: int = 6,
        filters: RetrievalFilters | None = None,
    ) -> ChatTurn:
        """Execute a chat turn and return the structured response."""
        sanitized_question = question.strip()
        if not sanitized_question:
            msg = "Chat question text is required."
            raise ValueError(msg)

        persona = self._load_persona_core()
        retriever_filters = filters or RetrievalFilters()

        requested_top = max(1, int(top))
        cfg_limit = coerce_int(self._chat_cfg.get("max_retrieved_chunks"))
        effective_top = min(requested_top, cfg_limit) if cfg_limit else requested_top

        result = self._retriever.search(
            sanitized_question,
            k=effective_top,
            filters=retriever_filters,
        )
        chunks = result.chunks
        citations_map = self._build_citations(chunks)

        if self._fake_mode:
            answer, citations = self._fake_answer(
                sanitized_question,
                persona,
                chunks,
            )
        else:
            answer, citations = self._real_answer(
                sanitized_question,
                persona,
                chunks,
                citations_map,
            )

        return ChatTurn(
            question=sanitized_question,
            answer=answer,
            persona=persona,
            citations=citations,
            retrieved_chunks=chunks,
            fake_mode=self._fake_mode,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _load_persona_core(self) -> PersonaCore:
        if not self._persona_path.exists():
            msg = "Persona core not found. Run `aijournal persona build` before using chat."
            raise RuntimeError(msg)
        try:
            persona_file = load_yaml_model(self._persona_path, PersonaCoreFile)
        except FileNotFoundError as exc:
            msg = "Persona core not found. Run `aijournal persona build` before using chat."
            raise RuntimeError(msg) from exc
        except ValidationError as exc:
            msg = f"Persona core failed validation: {exc}"
            raise RuntimeError(msg) from exc
        return persona_file.persona

    def _build_citations(
        self,
        chunks: Sequence[RetrievedChunk],
    ) -> dict[str, ChatCitation]:
        citations: dict[str, ChatCitation] = {}
        for chunk in chunks:
            citation = ChatCitation.from_chunk(chunk)
            citations[citation.code] = citation
        return citations

    def _fake_answer(
        self,
        question: str,
        persona: PersonaCore,
        chunks: Sequence[RetrievedChunk],
        *,
        prefix: str = "(fake)",
    ) -> tuple[str, list[ChatCitation]]:
        if not chunks:
            answer = (
                f"{prefix} No indexed journal entries matched '{question}'. "
                "Rebuild the index if you recently added notes."
            )
            return answer.strip(), []

        top_chunk = chunks[0]
        citation = ChatCitation.from_chunk(top_chunk)
        snippet = _truncate_text(top_chunk.text)
        claim_statement = persona.claims[0].statement if persona.claims else ""
        persona_clause = (
            f" This aligns with your persona focus on {claim_statement}." if claim_statement else ""
        )
        answer = (
            f"{prefix} On {top_chunk.date} you noted {snippet} {citation.marker}.{persona_clause}"
        )
        return answer.strip(), [citation]

    def _real_answer(
        self,
        question: str,
        persona: PersonaCore,
        chunks: Sequence[RetrievedChunk],
        citations_map: dict[str, ChatCitation],
    ) -> tuple[str, list[ChatCitation]]:
        if not chunks:
            return self._fake_answer(
                question,
                persona,
                chunks,
                prefix="(fallback)",
            )

        prompt = self._render_prompt(question, persona, chunks)
        try:
            payload: dict[str, Any] = run_ollama_agent(self._build_ollama_config(), prompt)
        except LLMResponseError:
            return self._fake_answer(
                question,
                persona,
                chunks,
                prefix="(fallback)",
            )

        answer = str(payload.get("answer") or "").strip()
        raw_citations = payload.get("citations") or []
        citations: list[ChatCitation] = []
        for item in raw_citations:
            code = str(item).strip()
            if not code:
                continue
            citation = citations_map.get(code)
            if citation and citation not in citations:
                citations.append(citation)

        if not answer:
            return self._fake_answer(
                question,
                persona,
                chunks,
                prefix="(fallback)",
            )

        if not citations and chunks:
            citations = [ChatCitation.from_chunk(chunks[0])]

        return answer, citations

    def _render_prompt(
        self,
        question: str,
        persona: PersonaCore,
        chunks: Sequence[RetrievedChunk],
    ) -> str:
        persona_summary = _persona_summary(persona)
        chunk_payload = [
            {
                "citation": ChatCitation.from_chunk(chunk).code,
                "date": chunk.date,
                "tags": chunk.tags,
                "text": chunk.text,
                "source_path": chunk.source_path,
            }
            for chunk in chunks
        ]
        context = {
            "question": question,
            "persona": persona_summary,
            "chunks": chunk_payload,
        }
        instructions = (
            "You are the aijournal chat assistant. Use the persona summary and "
            "retrieved journal chunks to answer the user's question. Always cite "
            "supporting chunks inline using [entry:<citation>] markers."
        )
        schema = (
            "Respond with JSON using the schema:\n"
            "{\n"
            '  "answer": string,\n'
            '  "citations": list[string]\n'
            "}\n"
            "Each item in citations must match one of the provided chunk citations."
        )
        return "\n\n".join(
            [
                instructions,
                schema,
                "Context:",
                json.dumps(context, indent=2, ensure_ascii=False),
            ],
        )

    def _build_ollama_config(self) -> OllamaConfig:
        overrides: dict[str, Any] = {}
        for key in ("model", "temperature", "seed", "max_tokens"):
            value = self._chat_cfg.get(key)
            if value is not None:
                overrides[key] = value

        merged = dict(self._config)
        merged.update(overrides)

        model_override = self._chat_cfg.get("model")
        host_override = self._chat_cfg.get("host")
        timeout_override = coerce_float(self._chat_cfg.get("timeout"))

        return build_ollama_config_from_mapping(
            merged,
            model=str(model_override) if isinstance(model_override, str) else None,
            host=str(host_override).strip() if isinstance(host_override, str) else None,
            timeout=timeout_override,
        )


def _truncate_text(text: str, limit: int = 120) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."


def _persona_summary(persona: PersonaCore, *, max_claims: int = 3) -> dict[str, Any]:
    claims = [
        {
            "id": claim.id,
            "statement": claim.statement,
            "strength": claim.strength,
            "status": claim.status,
        }
        for claim in persona.claims[:max_claims]
    ]
    profile = persona.profile or {}
    return {
        "profile": profile,
        "claims": claims,
    }


__all__ = [
    "ChatCitation",
    "ChatService",
    "ChatTurn",
]
