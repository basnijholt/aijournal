"""Service utilities for aijournal."""

from .embedding import DEFAULT_EMBED_DIM, EmbeddingBackend
from .ollama import LLMResponseError, OllamaConfig, OllamaTaskRunner
from .retriever import (
    RetrievalFilters,
    RetrievalResult,
    RetrievedChunk,
    Retriever,
)

__all__ = [
    "DEFAULT_EMBED_DIM",
    "EmbeddingBackend",
    "LLMResponseError",
    "OllamaConfig",
    "OllamaTaskRunner",
    "RetrievalFilters",
    "RetrievalResult",
    "RetrievedChunk",
    "Retriever",
]
