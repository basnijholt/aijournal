"""Service utilities for aijournal."""

from .chat import ChatCitation, ChatService, ChatTelemetry, ChatTurn
from .chat_api import build_chat_app
from .consolidator import (
    ClaimConflict,
    ClaimConsolidator,
    ClaimMergeOutcome,
    ClaimSignature,
)
from .embedding import DEFAULT_EMBED_DIM, EmbeddingBackend
from .feedback import FeedbackAdjustment, apply_chat_feedback, extract_claim_markers
from .ollama import (
    LLMResponseError,
    OllamaConfig,
    build_ollama_agent,
    build_ollama_config_from_mapping,
    build_ollama_model,
    resolve_ollama_base_url,
    resolve_ollama_host,
    run_ollama_agent,
)
from .retriever import (
    RetrievalFilters,
    RetrievalMeta,
    RetrievalResult,
    RetrievedChunk,
    Retriever,
)

__all__ = [
    "ChatCitation",
    "ChatService",
    "ChatTurn",
    "ChatTelemetry",
    "build_chat_app",
    "FeedbackAdjustment",
    "apply_chat_feedback",
    "extract_claim_markers",
    "ClaimConsolidator",
    "ClaimConflict",
    "ClaimMergeOutcome",
    "ClaimSignature",
    "DEFAULT_EMBED_DIM",
    "EmbeddingBackend",
    "LLMResponseError",
    "OllamaConfig",
    "build_ollama_agent",
    "build_ollama_config_from_mapping",
    "build_ollama_model",
    "run_ollama_agent",
    "resolve_ollama_base_url",
    "resolve_ollama_host",
    "RetrievalFilters",
    "RetrievalMeta",
    "RetrievalResult",
    "RetrievedChunk",
    "Retriever",
]
