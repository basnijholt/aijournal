"""Tests for the chat FastAPI service (chatd)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aijournal.api.chat import ChatResponse
from aijournal.cli import app
from aijournal.common.app_config import AppConfig
from aijournal.domain.persona import PersonaCore
from aijournal.io.yaml_io import dump_yaml
from aijournal.services.chat import ChatService, ChatTelemetry, ChatTurn
from aijournal.services.chat_api import build_chat_app
from tests.helpers import make_claim_atom, write_manifest, write_normalized_entry

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _fake_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")


@pytest.fixture
def capture_pipeline_mocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "aijournal.utils.time.now",
        lambda: datetime(2025, 10, 28, 9, 0, tzinfo=UTC),
    )

    def _ensure_file(path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    monkeypatch.setattr(
        "aijournal.commands.summarize.run_summarize",
        lambda date, *, progress, workspace=None, config=None: _ensure_file(
            tmp_path / "derived" / "summaries" / f"{date}.yaml",
            "summary",
        ),
    )

    monkeypatch.setattr(
        "aijournal.commands.facts.run_facts",
        lambda date, *, progress, claim_models, generate_preview, workspace=None, config=None: (
            None,
            _ensure_file(tmp_path / "derived" / "microfacts" / f"{date}.yaml", "facts"),
        ),
    )

    monkeypatch.setattr(
        "aijournal.commands.profile.run_profile_apply",
        lambda date, *, suggestions_path, auto_confirm, workspace=None: "applied",
    )

    monkeypatch.setattr(
        "aijournal.commands.profile_update.run_profile_update",
        lambda date, *, progress, generate_preview, workspace=None, config=None: _ensure_file(
            tmp_path / "derived" / "pending" / "profile_updates" / f"{date}-batch.yaml",
            "batch",
        ),
    )

    monkeypatch.setattr(
        "aijournal.commands.profile.load_profile_components",
        lambda *_, **__: (None, [object()]),
    )

    monkeypatch.setattr(
        "aijournal.commands.index.run_index_rebuild",
        lambda since, *, limit: "rebuild",
    )

    monkeypatch.setattr(
        "aijournal.commands.index.run_index_tail",
        lambda since, *, days, limit: "tail",
    )

    persona_states = [
        ("stale", ["needs rebuild"]),
        ("fresh", []),
    ]

    def _persona_state(root: Path) -> tuple[str, list[str]]:
        return persona_states.pop(0) if persona_states else ("fresh", [])

    monkeypatch.setattr("aijournal.commands.persona.persona_state", _persona_state)

    def _persona_build(profile, claims, *, config, root=None):
        path = _ensure_file(tmp_path / "derived" / "persona" / "persona_core.yaml", "persona")
        return path, True

    monkeypatch.setattr("aijournal.commands.persona.run_persona_build", _persona_build)

    monkeypatch.setattr(
        "aijournal.commands.pack.run_pack",
        lambda *args, **kwargs: None,
    )


def _init_workspace(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.chdir(base)
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert (
        runner.invoke(
            app,
            ["ops", "persona", "build"],
            env={"AIJOURNAL_FAKE_OLLAMA": "1"},
        ).exit_code
        == 0
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
            },
        ],
    )
    runner = CliRunner()
    assert (
        runner.invoke(
            app,
            ["ops", "index", "rebuild"],
            env={"AIJOURNAL_FAKE_OLLAMA": "1"},
        ).exit_code
        == 0
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
    config_dict = (
        yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    )
    config = AppConfig.model_validate(config_dict)
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
    assert "code" in answer["citations"][0]
    assert "marker" in answer["citations"][0]


def test_chatd_no_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _build_index(
        tmp_path,
        day="2025-02-03",
        entry_id="focus-entry",
        summary="Protected deep work blocks.",
    )

    config_path = tmp_path / "config" / "config.yaml"
    config_dict = (
        yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    )
    config = AppConfig.model_validate(config_dict)
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
    claims_path.write_text(dump_yaml(claims_payload, sort_keys=False), encoding="utf-8")

    def _fake_run(self, question: str, *, top: int = 6, filters=None) -> ChatTurn:  # type: ignore[override]
        telemetry = ChatTelemetry(
            retrieval_ms=4.0,
            chunk_count=0,
            retriever_source="stub",
            model="fake",
        )
        response = ChatResponse(
            answer="Signal from claim [claim:focus-claim] informs the response.",
            citations=[],
            clarifying_question=None,
            timestamp="2025-02-03T00:00:00Z",
        )
        return ChatTurn(
            question=question,
            answer=response.answer,
            response=response,
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
    config_dict = (
        yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    )
    config = AppConfig.model_validate(config_dict)
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


def test_capture_endpoint_streams_and_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_pipeline_mocks: None,
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    config_path = tmp_path / "config" / "config.yaml"
    config_dict = (
        yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    )
    config = AppConfig.model_validate(config_dict)
    app_instance = build_chat_app(tmp_path, config)

    with TestClient(app_instance) as client:
        payload = {
            "source": "stdin",
            "text": "Hello capture pipeline",
            "source_type": "journal",
            "apply_profile": "auto",
        }

        with client.stream("POST", "/capture", json=payload) as response:
            assert response.status_code == 200
            events = [json.loads(line) for line in response.iter_lines() if line]

        assert events, "Expected streamed events"
        run_id = events[0]["run_id"]
        assert run_id.startswith("capture-")
        assert events[0]["event"] == "preflight"
        assert events[-2]["event"] == "done"
        assert events[-1]["event"] == "result"
        assert all(event.get("run_id") == run_id for event in events if "run_id" in event)

        telemetry_rel = events[-1]["telemetry_path"]
        assert telemetry_rel
        telemetry_path = tmp_path / telemetry_rel
        assert telemetry_path.exists()

        result_response = client.get(f"/runs/{run_id}")
        assert result_response.status_code == 200
        result_payload = result_response.json()
        assert result_payload["run_id"] == run_id
        assert result_payload["telemetry_path"] == telemetry_rel
        assert result_payload["entries"]


def test_capture_run_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    config_path = tmp_path / "config" / "config.yaml"
    config_dict = (
        yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    )
    config = AppConfig.model_validate(config_dict)
    app_instance = build_chat_app(tmp_path, config)

    with TestClient(app_instance) as client:
        response = client.get("/runs/missing-run")
        assert response.status_code == 404
