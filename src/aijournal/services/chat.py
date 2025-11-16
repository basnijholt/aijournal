"""Chat orchestration service built on top of Retriever and persona core."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from aijournal.api.chat import ChatCitation, ChatCitationRef, ChatResponse
from aijournal.common.app_config import AppConfig
from aijournal.domain.chat import ChatTelemetry, ChatTurn
from aijournal.domain.index import RetrievedChunk  # noqa: TC001
from aijournal.domain.persona import PersonaCore
from aijournal.io.artifacts import load_artifact
from aijournal.services.ollama import (
    LLMResponseError,
    OllamaConfig,
    build_ollama_config_from_mapping,
    run_ollama_agent,
)
from aijournal.services.retriever import RetrievalFilters, Retriever
from aijournal.utils.coercion import coerce_int

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aijournal.common.meta import LLMResult

_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "planning": (
        "plan",
        "schedule",
        "calend",
        "organize",
        "prioritize",
        "roadmap",
    ),
    "reflection": (
        "reflect",
        "feel",
        "learn",
        "retrospective",
        "improve next time",
    ),
    "qa_about_me": (
        "what do i value",
        "tell me about me",
        "remind me who i am",
        "profile say",
        "what are my",
    ),
    "meta": (
        "what can you do",
        "help works",
        "capabilities",
        "instructions",
    ),
}

_ADVICE_VERBS = ("should i", "how do i", "help me", "guide me", "recommend")


class ChatService:
    """Minimal chat orchestrator that composes persona + retrieval."""

    def __init__(
        self,
        root: Path,
        config: AppConfig | None = None,
        *,
        retriever: Retriever | None = None,
    ) -> None:
        self._root = Path(root)
        self._config: AppConfig = config or AppConfig()
        self._persona_path = self._root / "derived" / "persona" / "persona_core.yaml"
        self._fake_mode = os.getenv("AIJOURNAL_FAKE_OLLAMA") == "1"
        self._retriever = retriever or Retriever(self._root, self._config)

        self._chat_cfg = self._config.chat

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
        intent = self._classify_intent(sanitized_question)

        requested_top = max(1, int(top))
        cfg_limit = self._chat_cfg.max_retrieved_chunks
        effective_top = min(requested_top, cfg_limit) if cfg_limit else requested_top

        search_started = time.perf_counter()
        result = self._retriever.search(
            sanitized_question,
            k=effective_top,
            filters=retriever_filters,
        )
        retrieval_ms = (time.perf_counter() - search_started) * 1000.0

        chunks = result.chunks
        citations_map = self._build_citations(chunks)
        allow_follow_up = self._allow_follow_up(persona)

        if self._fake_mode:
            answer, citations, clarifying, response = self._fake_answer(
                sanitized_question,
                persona,
                chunks,
                intent=intent,
                allow_follow_up=allow_follow_up,
            )
        else:
            answer, citations, clarifying, response = self._real_answer(
                sanitized_question,
                persona,
                chunks,
                citations_map,
                intent=intent,
                allow_follow_up=allow_follow_up,
            )

        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        telemetry = ChatTelemetry(
            retrieval_ms=retrieval_ms,
            chunk_count=len(chunks),
            retriever_source=result.meta.source,
            model=self._effective_model_name(),
        )
        telemetry_payload = telemetry.model_dump(mode="python")
        response = response.model_copy(
            update={
                "answer": answer,
                "telemetry": telemetry_payload,
                "timestamp": response.timestamp or timestamp,
            },
        )
        return ChatTurn(
            question=sanitized_question,
            answer=answer,
            response=response,
            persona=persona,
            citations=citations,
            retrieved_chunks=chunks,
            fake_mode=self._fake_mode,
            intent=intent,
            clarifying_question=clarifying,
            telemetry=telemetry,
            timestamp=timestamp,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _load_persona_core(self) -> PersonaCore:
        if not self._persona_path.exists():
            msg = "Persona core not found. Run `aijournal persona build` before using chat."
            raise RuntimeError(msg)
        try:
            persona_artifact = load_artifact(self._persona_path, PersonaCore)
        except FileNotFoundError as exc:
            msg = "Persona core not found. Run `aijournal persona build` before using chat."
            raise RuntimeError(msg) from exc
        except ValidationError as exc:
            msg = f"Persona core failed validation: {exc}"
            raise RuntimeError(msg) from exc
        return persona_artifact.data

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
        intent: str,
        allow_follow_up: bool,
        prefix: str = "(fake)",
    ) -> tuple[str, list[ChatCitation], str | None, ChatResponse]:
        if not chunks:
            answer = (
                f"{prefix} No indexed journal entries matched '{question}'. "
                "Rebuild the index if you recently added notes."
            )
            response = ChatResponse(
                answer=answer.strip(),
                citations=[],
                timestamp=None,
            )
            return answer.strip(), [], None, response

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
        clarifying: str | None = None
        if allow_follow_up:
            clarifying = self._generate_clarifying_question(intent, question)
        response = ChatResponse(
            answer=answer.strip(),
            citations=[ChatCitationRef(code=citation.code)],
            clarifying_question=clarifying,
            timestamp=None,
        )
        return answer.strip(), [citation], clarifying, response

    def _real_answer(
        self,
        question: str,
        persona: PersonaCore,
        chunks: Sequence[RetrievedChunk],
        citations_map: dict[str, ChatCitation],
        *,
        intent: str,
        allow_follow_up: bool,
    ) -> tuple[str, list[ChatCitation], str | None, ChatResponse]:
        if not chunks:
            msg = (
                "No journal chunks were retrieved for this chat question; "
                "unable to generate a grounded answer."
            )
            raise RuntimeError(msg)

        prompt = self._render_prompt(
            question,
            persona,
            chunks,
            intent=intent,
            allow_follow_up=allow_follow_up,
        )
        result: LLMResult[ChatResponse] = run_ollama_agent(
            self._build_ollama_config(),
            prompt,
            output_type=ChatResponse,
            retries=self._config.llm.retries,
        )
        response = result.payload
        timestamp_str = datetime.now(tz=UTC).isoformat()
        if not response.timestamp:
            response = response.model_copy(update={"timestamp": timestamp_str})
        answer = response.answer.strip()

        if not answer:
            msg = "Chat model returned an empty answer."
            raise LLMResponseError(msg)

        citations: list[ChatCitation] = []
        missing_codes: list[str] = []
        for citation_ref in response.citations:
            code = citation_ref.code.strip()
            if not code:
                continue
            citation = citations_map.get(code)
            if citation is None:
                missing_codes.append(code)
                continue
            if citation not in citations:
                citations.append(citation)

        if missing_codes:
            msg = (
                f"Chat model referenced unknown citation codes: {', '.join(sorted(missing_codes))}."
            )
            raise LLMResponseError(
                msg,
            )
        if not citations:
            msg = "Chat model did not provide any citations."
            raise LLMResponseError(msg)

        clarifying: str | None = None
        if allow_follow_up and response.clarifying_question:
            clarifying_candidate = response.clarifying_question.strip()
            clarifying = clarifying_candidate or None

        response = response.model_copy(
            update={"citations": [ChatCitationRef(code=c.code) for c in citations]},
        )
        return answer, citations, clarifying, response

    def _render_prompt(
        self,
        question: str,
        persona: PersonaCore,
        chunks: Sequence[RetrievedChunk],
        *,
        intent: str,
        allow_follow_up: bool,
    ) -> str:
        persona_summary = _persona_summary(persona)
        chunk_payload = [
            {
                "citation": ChatCitation.from_chunk(chunk).code,
                "date": chunk.date,
                "tags": chunk.tags,
                "text": chunk.text,
                "chunk_type": chunk.chunk_type,
                "source_path": chunk.source_path,
            }
            for chunk in chunks
        ]
        context = {
            "question": question,
            "persona": persona_summary,
            "chunks": chunk_payload,
            "intent": intent,
            "allow_follow_up": allow_follow_up,
        }
        instructions = (
            "You are the aijournal chat assistant. Use the persona summary and "
            "retrieved journal chunks to answer the user's question. Always cite "
            "supporting chunks inline using markers that match their chunk_type (e.g., "
            "[entry:<citation>], [summary:<citation>], or [microfact:<citation>]) and prefer "
            "the exact prefix supplied in the chunk metadata so downstream feedback can work. "
            "Whenever `persona.claims` is non-empty, include at least one [claim:<id>] marker that references "
            "the most relevant persona claim (use the IDs provided) so feedback can adjust it. "
            "If a follow-up question would materially clarify intent and the caller allows probing, "
            "include it in the response. Keep follow-ups short and focused."
        )
        schema = (
            "Respond with JSON using the schema:\n"
            "{\n"
            '  "answer": string,\n'
            '  "citations": [{"code": "2025-10-26-morning-planning-session#p0"}, {"code": "claim:abc123"}],\n'
            '  "clarifying_question": string | null\n'
            "}\n"
            "Each citation code must match exactly one of the chunk citation codes provided in the context. "
            "Citations must be objects with a 'code' field, not plain strings. "
            "Do NOT add 'entry:' or 'claim:' prefixes to the codes - those are only used in the answer text markers."
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
        model_override = self._chat_cfg.model
        host_override = self._chat_cfg.host
        timeout_override = self._chat_cfg.timeout
        temperature_override = self._chat_cfg.temperature
        seed_override = self._chat_cfg.seed
        max_tokens_override = self._chat_cfg.max_tokens

        return build_ollama_config_from_mapping(
            self._config,
            model=str(model_override) if isinstance(model_override, str) else None,
            host=str(host_override).strip() if isinstance(host_override, str) else None,
            timeout=timeout_override,
            temperature=temperature_override,
            seed=seed_override,
            max_tokens=max_tokens_override,
        )

    # ------------------------------------------------------------------
    # Intent and follow-up helpers
    # ------------------------------------------------------------------
    def _effective_model_name(self) -> str:
        override = self._chat_cfg.model
        if isinstance(override, str) and override.strip():
            return override.strip()
        config_model = self._config.model
        if isinstance(config_model, str) and config_model.strip():
            return str(config_model).strip()
        env_model = os.getenv("AIJOURNAL_MODEL")
        if env_model and env_model.strip():
            return env_model.strip()
        return "llama3.1:8b-instruct"

    def model_name(self) -> str:
        """Public accessor for the effective model name."""
        return self._effective_model_name()

    def _classify_intent(self, question: str) -> str:
        text = question.lower().strip()
        for intent, keywords in _INTENT_KEYWORDS.items():
            if any(keyword in text for keyword in keywords):
                return intent
        if any(verb in text for verb in _ADVICE_VERBS):
            return "advice"
        if "goal" in text or "focus" in text:
            return "planning"
        if text.startswith(("why", "what", "how")):
            return "advice"
        return "advice"

    def _allow_follow_up(self, persona: PersonaCore) -> bool:
        prefs = persona.profile.get("coaching_prefs") if persona.profile else {}
        probing = prefs.get("probing") if isinstance(prefs, dict) else None
        if not isinstance(probing, dict):
            return True
        max_questions = coerce_int(probing.get("max_questions"))
        if max_questions is None:
            return True
        return max_questions > 0

    def _generate_clarifying_question(self, intent: str, question: str) -> str:
        base = question.strip().rstrip("?.!")
        if intent == "planning":
            return "Which timeframe should we focus on planning for?"
        if intent == "reflection":
            return "Do you want to explore what felt energizing or draining about that?"
        if intent == "qa_about_me":
            return "Should I pull a quick summary of your core values or recent habits?"
        if intent == "meta":
            return "Would you like tips on how this chat can support your workflows?"
        if len(base.split()) <= 6:
            return "Could you share a bit more context so I can point to the right evidence?"
        return "Do you want the answer to emphasize next actions or background reasoning?"


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
