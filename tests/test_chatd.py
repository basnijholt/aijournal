"""Tests for the chat FastAPI service (chatd)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aijournal.cli import app
from aijournal.models import PersonaCore
from aijournal.services import ChatService, ChatTelemetry, ChatTurn, build_chat_app
from tests.helpers import make_claim_atom, write_manifest, write_normalized_entry


@pytest.fixture(autouse=True)
def _fake_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")


def _init_workspace(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.chdir(base)
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert (
        runner.invoke(app, ["persona", "build"], env={"AIJOURNAL_FAKE_OLLAMA": "1"}).exit_code == 0
    )


def _build_index(base: Path, *, day: str, entry_id: str, summary: str) -> None:
    write_normalized_entry(
        base,
        date=day,
        entry_id=entry_id,
        summary=summary,
        tags=["focus"],
    )
    write_manifest(
        base,
        [
            {
                "id": entry_id,
                "hash": f"hash-{entry_id}",
                "source_type": "journal",
            }
        ],
    )
    runner = CliRunner()
    assert (
        runner.invoke(app, ["index", "rebuild"], env={"AIJOURNAL_FAKE_OLLAMA": "1"}).exit_code == 0
    )


def test_chatd_streams_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _build_index(
        tmp_path,
        day="2025-02-03",
        entry_id="focus-entry",
        summary="Protected deep work blocks.",
    )

    config_path = tmp_path / "config" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    app_instance = build_chat_app(tmp_path, config)
    client = TestClient(app_instance)

    response = client.post("/chat", json={"question": "What did I note?"})
    assert response.status_code == 200
    lines = response.content.decode("utf-8").strip().splitlines()
    assert len(lines) == 2
    meta = json.loads(lines[0])
    answer = json.loads(lines[1])
    assert meta["event"] == "meta"
    session_id = meta["session_id"]
    assert session_id
    assert meta["feedback"] is None
    session_dir = tmp_path / "derived" / "chat_sessions" / session_id
    assert session_dir.exists()
    assert answer["event"] == "answer"
    assert answer["citations"], "Expected citations in streamed answer"


def test_chatd_no_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _build_index(
        tmp_path,
        day="2025-02-03",
        entry_id="focus-entry",
        summary="Protected deep work blocks.",
    )

    config_path = tmp_path / "config" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    app_instance = build_chat_app(tmp_path, config)
    client = TestClient(app_instance)

    response = client.post("/chat", json={"question": "What did I note?", "save": False})
    assert response.status_code == 200
    meta = json.loads(response.content.decode("utf-8").splitlines()[0])
    session_id = meta["session_id"]
    sessions_dir = tmp_path / "derived" / "chat_sessions"
    if sessions_dir.exists():
        assert session_id not in {p.name for p in sessions_dir.iterdir()}


def test_chatd_feedback_adjusts_claims(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)

    claims_path = tmp_path / "profile" / "claims.yaml"
    claims_payload = {"claims": [make_claim_atom("focus-claim", "Focus work", strength=0.5)]}
    claims_path.write_text(yaml.safe_dump(claims_payload, sort_keys=False), encoding="utf-8")

    def _fake_run(self, question: str, *, top: int = 6, filters=None) -> ChatTurn:  # type: ignore[override]
        telemetry = ChatTelemetry(
            retrieval_ms=4.0,
            chunk_count=0,
            retriever_source="stub",
            model="fake",
        )
        return ChatTurn(
            question=question,
            answer="Signal from claim [claim:focus-claim] informs the response.",
            persona=PersonaCore(),
            citations=[],
            retrieved_chunks=[],
            fake_mode=True,
            intent="advice",
            clarifying_question=None,
            telemetry=telemetry,
            timestamp="2025-02-03T00:00:00Z",
        )

    monkeypatch.setattr(ChatService, "run", _fake_run, raising=True)

    config_path = tmp_path / "config" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    app_instance = build_chat_app(tmp_path, config)
    client = TestClient(app_instance)

    response = client.post(
        "/chat",
        json={"question": "Need context", "feedback": "down"},
    )
    assert response.status_code == 200

    claims_after = yaml.safe_load(claims_path.read_text(encoding="utf-8"))
    strength = claims_after["claims"][0]["strength"]
    assert pytest.approx(strength, rel=1e-4) == 0.45

    meta = json.loads(response.content.decode("utf-8").splitlines()[0])
    assert meta["feedback"] == "down"
    assert meta["feedback_claims"] == ["focus-claim"]
