"""Embedding helpers shared across indexing and retrieval."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING

import httpx

from aijournal.common.constants import DEFAULT_EMBED_DIM, EMBED_TIMEOUT
from aijournal.services.ollama import resolve_ollama_host

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass
class EmbeddingBackend:
    """Thin wrapper that returns deterministic vectors in fake mode."""

    model: str
    host: str | None = None
    fake_mode: bool = False
    dimension: int | None = None
    _base_host: str = field(init=False)

    def __post_init__(self) -> None:
        self._base_host = resolve_ollama_host(self.host)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        if not texts:
            return vectors
        if self.fake_mode:
            return [self._fake_embed(text) for text in texts]

        endpoint = f"{self._base_host}/api/embeddings"
        try:
            with httpx.Client(timeout=EMBED_TIMEOUT) as session:
                for text in texts:
                    response = session.post(
                        endpoint,
                        json={
                            "model": self.model,
                            "prompt": text,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                    vector = payload.get("embedding")
                    if not isinstance(vector, list):
                        msg = "Ollama embedding response missing vector payload"
                        raise RuntimeError(msg)
                    if self.dimension is None:
                        self.dimension = len(vector)
                    vectors.append([float(value) for value in vector])
        except httpx.HTTPError as exc:
            msg = f"Ollama embedding request failed: {exc}"
            raise RuntimeError(msg) from exc
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
