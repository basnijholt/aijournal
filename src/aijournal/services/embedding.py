"""Embedding helpers shared across indexing and retrieval."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING

from ollama import Client

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_EMBED_DIM = 384


@dataclass
class EmbeddingBackend:
    """Thin wrapper that returns deterministic vectors in fake mode."""

    model: str
    host: str | None = None
    fake_mode: bool = False
    dimension: int | None = None
    _client: Client | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if not self.fake_mode and self.dimension is None:
            # Delay Ollama client creation until needed to keep tests fast
            self._client = Client(host=self.host) if self.host else Client()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        if not texts:
            return vectors
        if self.fake_mode:
            return [self._fake_embed(text) for text in texts]

        if self._client is None:  # pragma: no cover - real mode lazy init
            self._client = Client(host=self.host) if self.host else Client()
        for text in texts:
            response = self._client.embeddings(model=self.model, prompt=text)
            if isinstance(response, dict):
                vector = response.get("embedding")
            elif hasattr(response, "model_dump"):
                data = response.model_dump()
                vector = data.get("embedding")
            else:
                vector = getattr(response, "embedding", None)
            if not isinstance(vector, list):
                msg = "Ollama embedding response missing vector payload"
                raise RuntimeError(msg)
            if self.dimension is None:
                self.dimension = len(vector)
            vectors.append([float(value) for value in vector])
        return vectors

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0] if text else [0.0] * self.dim

    @property
    def dim(self) -> int:
        return self.dimension or DEFAULT_EMBED_DIM

    def _fake_embed(self, text: str) -> list[float]:
        seed = int.from_bytes(sha256(text.encode("utf-8")).digest()[:8], "big")
        rng = random.Random(seed)
        dim = self.dimension or DEFAULT_EMBED_DIM
        self.dimension = dim
        return [rng.uniform(-1.0, 1.0) for _ in range(dim)]


__all__ = ["DEFAULT_EMBED_DIM", "EmbeddingBackend"]
