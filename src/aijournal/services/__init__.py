"""Service utilities for aijournal."""

from .chat import ChatCitation, ChatService, ChatTurn
from .consolidator import (
    ClaimConflict,
    ClaimConsolidator,
    ClaimMergeOutcome,
    ClaimSignature,
)
from .embedding import DEFAULT_EMBED_DIM, EmbeddingBackend
from .ollama import LLMResponseError, OllamaConfig, OllamaTaskRunner
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
    "ClaimConsolidator",
    "ClaimConflict",
    "ClaimMergeOutcome",
    "ClaimSignature",
    "DEFAULT_EMBED_DIM",
    "EmbeddingBackend",
    "LLMResponseError",
    "OllamaConfig",
    "OllamaTaskRunner",
    "RetrievalFilters",
    "RetrievalMeta",
    "RetrievalResult",
    "RetrievedChunk",
    "Retriever",
]
