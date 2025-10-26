from __future__ import annotations

from aijournal.services import OllamaConfig, OllamaTaskRunner


class _FakeResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, str]:
        return {"response": self._payload}


class _FakeClient:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def generate(self, **_: object) -> _FakeResponse:
        return _FakeResponse(self._payload)


def test_ollama_runner_handles_model_dump_responses(monkeypatch) -> None:
    runner = OllamaTaskRunner(OllamaConfig(model="fake-model"))
    runner._client = _FakeClient('{"ok": true, "value": 42}')  # type: ignore[attr-defined]

    result = runner.generate_json("prompt")

    assert result == {"ok": True, "value": 42}
