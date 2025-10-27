"""CLI coverage for the new chat command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app
from aijournal.models import PersonaCore
from aijournal.services.chat import ChatService, ChatTelemetry, ChatTurn
from tests.helpers import make_claim_atom, write_manifest, write_normalized_entry

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fake_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")


def _init_workspace(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(base)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0, init_result.stdout
    persona_result = runner.invoke(
        app,
        ["persona", "build"],
        env={"AIJOURNAL_FAKE_OLLAMA": "1"},
    )
    assert persona_result.exit_code == 0, persona_result.stdout


def _build_index(
    base: Path,
    *,
    day: str,
    entry_id: str,
    summary: str,
    tags: list[str] | None = None,
) -> None:
    write_normalized_entry(
        base,
        date=day,
        entry_id=entry_id,
        summary=summary,
        tags=tags,
    )
    write_manifest(
        base,
        [
            {"id": entry_id, "hash": f"hash-{entry_id}", "source_type": "journal"},
        ],
    )
    rebuild = runner.invoke(
        app,
        ["index", "rebuild"],
        env={"AIJOURNAL_FAKE_OLLAMA": "1"},
    )
    assert rebuild.exit_code == 0, rebuild.stdout


def test_chat_fake_mode_outputs_answer_with_citation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    entry_id = "2025-02-03-focus-notes"
    _build_index(
        tmp_path,
        day="2025-02-03",
        entry_id=entry_id,
        summary="Protected two focus blocks and captured deep work ideas.",
        tags=["focus", "planning"],
    )

    result = runner.invoke(
        app,
        ["chat", "How did I protect my focus last week?"],
        env={"AIJOURNAL_FAKE_OLLAMA": "1"},
    )
    assert result.exit_code == 0, result.stdout
    output = result.stdout or result.output
    assert "Chat response (fake mode)" in output
    assert "(fake)" in output
    assert f"[entry:{entry_id}#p0]" in output
    assert "Citations:" in output
    assert "tags: focus, planning" in output
    assert "Clarifying question:" in output
    assert "Telemetry:" in output

    session_line = next(line for line in output.splitlines() if line.startswith("Session:"))
    session_id = session_line.split(":", 1)[1].strip()
    session_dir = tmp_path / "derived" / "chat_sessions" / session_id
    assert session_dir.exists()
    transcript = session_dir / "transcript.jsonl"
    summary = session_dir / "summary.yaml"
    learnings = session_dir / "learnings.yaml"
    assert transcript.exists()
    assert summary.exists()
    assert learnings.exists()

    lines = transcript.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    payload = json.loads(lines[-1])
    assert payload["role"] == "assistant"
    assert "[entry:" in payload["text"]
    assert payload["clarifying_question"]
    assert payload["telemetry"]["chunk_count"] == 1
    assert payload.get("feedback") is None


def test_chat_errors_when_index_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        ["chat", "anything"],
        env={"AIJOURNAL_FAKE_OLLAMA": "1"},
    )
    assert result.exit_code != 0
    combined = (result.stderr or "") + (result.stdout or result.output or "")
    assert "Retrieval index not available" in combined


def test_chat_service_requires_persona_core(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"], env={"AIJOURNAL_FAKE_OLLAMA": "1"}, catch_exceptions=False)
    config = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))
    service = ChatService(tmp_path, config)
    try:
        with pytest.raises(RuntimeError, match="Persona core not found"):
            service.run("Need a summary")
    finally:
        service.close()


def test_chat_service_builds_config_with_overrides(tmp_path: Path) -> None:
    class DummyRetriever:
        def close(self) -> None:
            pass

    config = {
        "model": "global-model",
        "temperature": "0.1",
        "chat": {
            "model": "chat-model",
            "temperature": "0.9",
            "seed": "123",
            "max_tokens": "500",
            "timeout": "45.5",
            "host": "http://chat-host:11434",
        },
    }

    service = ChatService(tmp_path, config, retriever=DummyRetriever())
    try:
        cfg = service._build_ollama_config()
    finally:
        service.close()

    assert cfg.model == "chat-model"
    assert cfg.temperature == pytest.approx(0.9)
    assert cfg.seed == 123
    assert cfg.max_tokens == 500
    assert cfg.timeout == pytest.approx(45.5)
    assert cfg.host == "http://chat-host:11434"


def test_chat_no_save_skips_transcript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _build_index(
        tmp_path,
        day="2025-02-03",
        entry_id="note",
        summary="Captured priorities.",
        tags=["focus"],
    )

    result = runner.invoke(
        app,
        ["chat", "Remind me of priorities", "--no-save"],
        env={"AIJOURNAL_FAKE_OLLAMA": "1"},
    )
    assert result.exit_code == 0, result.stdout
    sessions_dir = tmp_path / "derived" / "chat_sessions"
    if sessions_dir.exists():
        assert not any(sessions_dir.iterdir()), "Expected no sessions when save disabled"


def test_chat_feedback_adjusts_claim_strength(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner_local = CliRunner()
    _init_workspace(tmp_path, monkeypatch)

    claims_path = tmp_path / "profile" / "claims.yaml"
    claims_payload = {"claims": [make_claim_atom("focus-claim", "Focus work", strength=0.5)]}
    claims_path.write_text(yaml.safe_dump(claims_payload, sort_keys=False), encoding="utf-8")

    def _fake_run(self, question: str, *, top: int = 6, filters=None) -> ChatTurn:  # type: ignore[override]
        telemetry = ChatTelemetry(
            retrieval_ms=5.0,
            chunk_count=0,
            retriever_source="stub",
            model="fake",
        )
        return ChatTurn(
            question=question,
            answer="It aligns with your focus routines [claim:focus-claim].",
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

    result = runner_local.invoke(
        app,
        ["chat", "Remind me", "--feedback", "up", "--no-save"],
        env={"AIJOURNAL_FAKE_OLLAMA": "1"},
    )
    assert result.exit_code == 0, result.stdout

    claims_after = yaml.safe_load(claims_path.read_text(encoding="utf-8"))
    updated_strength = claims_after["claims"][0]["strength"]
    assert pytest.approx(updated_strength, rel=1e-4) == 0.53

    pending_dir = tmp_path / "derived" / "pending" / "profile_updates"
    files = list(pending_dir.glob("feedback_*.yaml"))
    assert files, "Expected feedback file queued"
