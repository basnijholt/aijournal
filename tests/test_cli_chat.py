"""CLI coverage for the new chat command."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import yaml

from aijournal.api.chat import ChatResponse
from aijournal.cli import app
from aijournal.common.app_config import AppConfig
from aijournal.common.meta import ArtifactKind
from aijournal.domain.chat_sessions import ChatSessionLearnings, ChatSessionSummary
from aijournal.domain.persona import PersonaCore
from aijournal.io.artifacts import load_artifact
from aijournal.io.yaml_io import dump_yaml
from aijournal.services.chat import ChatService, ChatTelemetry, ChatTurn
from tests.helpers import make_claim_atom, write_manifest, write_normalized_entry

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def _fake_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")


def _ensure_persona(cli_runner: CliRunner) -> None:
    persona_result = cli_runner.invoke(app, ["ops", "persona", "build"])
    assert persona_result.exit_code == 0, persona_result.stdout


def _build_index(
    base: Path,
    cli_runner: CliRunner,
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
    rebuild = cli_runner.invoke(app, ["ops", "index", "rebuild"])
    assert rebuild.exit_code == 0, rebuild.stdout


def test_chat_fake_mode_outputs_answer_with_citation(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _ensure_persona(cli_runner)
    entry_id = "2025-02-03-focus-notes"
    _build_index(
        cli_workspace,
        cli_runner,
        day="2025-02-03",
        entry_id=entry_id,
        summary="Protected two focus blocks and captured deep work ideas.",
        tags=["focus", "planning"],
    )

    result = cli_runner.invoke(app, ["chat", "How did I protect my focus last week?"])
    assert result.exit_code == 0, result.stdout
    output = result.stdout or result.output
    assert "Chat response (fake mode)" in output
    assert "(fake)" in output
    assert f"[entry:{entry_id}#p0]" in output
    assert "Citations:" in output
    assert "tags" in output.lower()
    assert "focus" in output
    assert "planning" in output
    assert "Clarifying question:" in output
    assert "Telemetry:" in output

    session_line = next(line for line in output.splitlines() if line.startswith("Session:"))
    session_id = session_line.split(":", 1)[1].strip()
    session_dir = cli_workspace / "derived" / "chat_sessions" / session_id
    assert session_dir.exists()
    transcript = session_dir / "transcript.json"
    summary = session_dir / "summary.yaml"
    learnings = session_dir / "learnings.yaml"
    assert transcript.exists()
    assert summary.exists()
    assert learnings.exists()

    transcript_payload = json.loads(transcript.read_text(encoding="utf-8"))
    assert transcript_payload["kind"] == "chat.transcript"
    turns = transcript_payload["data"]["turns"]
    assert len(turns) == 1
    entry = turns[0]
    assert entry["answer"].count("[entry:") >= 1
    assert entry["clarifying_question"]
    assert entry["telemetry"]["chunk_count"] == 1
    assert entry.get("feedback") is None

    summary_artifact = load_artifact(summary, ChatSessionSummary)
    assert summary_artifact.kind is ArtifactKind.CHAT_SUMMARY
    summary_data = summary_artifact.data
    assert summary_data.turn_count == 1
    assert summary_data.intent_counts
    assert summary_data.last_citations

    learnings_artifact = load_artifact(learnings, ChatSessionLearnings)
    assert learnings_artifact.kind is ArtifactKind.CHAT_LEARNINGS
    learnings_data = learnings_artifact.data
    assert len(learnings_data.learnings) == 1
    learning_entry = learnings_data.learnings[0]
    assert learning_entry.citations
    assert learning_entry.telemetry.chunk_count == 1


def test_chat_errors_when_index_missing(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _ensure_persona(cli_runner)

    result = cli_runner.invoke(app, ["chat", "anything"])
    assert result.exit_code != 0
    combined = (result.stderr or "") + (result.stdout or result.output or "")
    assert "Retrieval index not available" in combined


def test_chat_service_requires_persona_core(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_runner: CliRunner,
) -> None:
    monkeypatch.chdir(tmp_path)
    cli_runner.invoke(app, ["init"], catch_exceptions=False)
    config_dict = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    config = AppConfig.model_validate(config_dict)
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

    config_dict = {
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
    config = AppConfig.model_validate(config_dict)

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
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _ensure_persona(cli_runner)
    _build_index(
        cli_workspace,
        cli_runner,
        day="2025-02-03",
        entry_id="note",
        summary="Captured priorities.",
        tags=["focus"],
    )

    result = cli_runner.invoke(app, ["chat", "Remind me of priorities", "--no-save"])
    assert result.exit_code == 0, result.stdout
    sessions_dir = cli_workspace / "derived" / "chat_sessions"
    if sessions_dir.exists():
        artifacts = [p for p in sessions_dir.iterdir() if p.name != ".gitkeep"]
        assert not artifacts, "Expected no sessions when save disabled"


def test_chat_feedback_adjusts_claim_strength(
    cli_workspace: Path,
    cli_runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_persona(cli_runner)

    claims_path = cli_workspace / "profile" / "claims.yaml"
    claims_payload = {"claims": [make_claim_atom("focus-claim", "Focus work", strength=0.5)]}
    claims_path.write_text(dump_yaml(claims_payload, sort_keys=False), encoding="utf-8")

    def _fake_run(self, question: str, *, top: int = 6, filters=None) -> ChatTurn:  # type: ignore[override]
        telemetry = ChatTelemetry(
            retrieval_ms=5.0,
            chunk_count=0,
            retriever_source="stub",
            model="fake",
        )
        response = ChatResponse(
            answer="It aligns with your focus routines [claim:focus-claim].",
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

    result = cli_runner.invoke(app, ["chat", "Remind me", "--feedback", "up", "--no-save"])
    assert result.exit_code == 0, result.stdout

    claims_after = yaml.safe_load(claims_path.read_text(encoding="utf-8"))
    updated_strength = claims_after["claims"][0]["strength"]
    assert pytest.approx(updated_strength, rel=1e-4) == 0.53

    pending_dir = cli_workspace / "derived" / "pending" / "profile_updates"
    files = list(pending_dir.glob("feedback_*.yaml"))
    assert files, "Expected feedback file queued"
    artifact = yaml.safe_load(files[0].read_text(encoding="utf-8"))
    data = artifact.get("data", {})
    assert data.get("feedback") == "up"
    assert data.get("events", [])[0]["claim_id"] == "focus-claim"
