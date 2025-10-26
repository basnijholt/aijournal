"""CLI coverage for the new chat command."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app
from aijournal.services.chat import ChatService
from tests.helpers import write_manifest, write_normalized_entry

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
