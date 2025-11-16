from __future__ import annotations

from typing import Any, Self

import httpx
import pytest

from aijournal.services.embedding import EmbeddingBackend


class _DummyResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        """No-op for tests."""

    def json(self) -> dict[str, Any]:
        return self._payload


def test_fake_mode_returns_deterministic_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aijournal.services.embedding.resolve_ollama_host",
        lambda host: host or "http://ollama",
    )
    backend = EmbeddingBackend(model="fake", fake_mode=True, dimension=4)

    first = backend.embed(["alpha", "beta"])
    second = backend.embed(["alpha"])

    assert len(first) == 2
    assert backend.dim == 4
    assert first[0] == second[0]
    assert backend.embed([]) == []


def test_embed_makes_http_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class DummyClient:
        def __init__(self, *, timeout: float | None = None) -> None:
            self.timeout = timeout
            self._requests = calls

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def post(self, url: str, json: dict[str, Any]) -> _DummyResponse:
            self._requests.append((url, json))
            return _DummyResponse({"embedding": [1, 2, 3]})

    monkeypatch.setattr(
        "aijournal.services.embedding.resolve_ollama_host",
        lambda host: host or "http://ollama",
    )
    monkeypatch.setattr("httpx.Client", DummyClient)

    backend = EmbeddingBackend(model="real", host="http://ollama", fake_mode=False)
    vectors = backend.embed(["text-one", "text-two"])

    assert vectors == [[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]]
    assert backend.dimension == 3
    assert calls == [
        ("http://ollama/api/embeddings", {"model": "real", "prompt": "text-one"}),
        ("http://ollama/api/embeddings", {"model": "real", "prompt": "text-two"}),
    ]


def test_embed_translates_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class ErrorClient:
        def __init__(self, *, timeout: float | None = None) -> None:
            self.timeout = timeout

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def post(self, *_: object, **__: object) -> httpx.Response:
            msg = "boom"
            raise httpx.HTTPError(msg)

    monkeypatch.setattr(
        "aijournal.services.embedding.resolve_ollama_host",
        lambda host: host or "http://ollama",
    )
    monkeypatch.setattr("httpx.Client", ErrorClient)

    backend = EmbeddingBackend(model="real", host="http://ollama")

    with pytest.raises(RuntimeError, match="embedding request failed: boom"):
        backend.embed(["text"])


def test_embed_one_returns_zero_vector_for_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aijournal.services.embedding.resolve_ollama_host",
        lambda host: host or "http://ollama",
    )
    backend = EmbeddingBackend(model="fake", fake_mode=True)
    backend.dimension = 5

    assert backend.embed_one("") == [0.0] * 5
