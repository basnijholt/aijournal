"""Service utilities for aijournal."""

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
