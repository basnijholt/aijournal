"""Tests for `aijournal facts` using fake Ollama outputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from aijournal.cli import app
from aijournal.io.yaml_io import dump_yaml
from tests.helpers import write_daily_summary

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner

DATE = "2025-02-03"
ENTRY_ID = "2025-02-03-sync-notes"


def _write_normalized(workspace: Path) -> Path:
    normalized = workspace / "data" / "normalized" / DATE / f"{ENTRY_ID}.yaml"
    normalized.parent.mkdir(parents=True, exist_ok=True)
    normalized.write_text(
        dump_yaml(
            {
                "id": ENTRY_ID,
                "created_at": "2025-02-03T14:05:00Z",
                "source_path": f"data/journal/2025/02/03/{ENTRY_ID}.md",
                "title": "Sync Notes",
                "tags": ["team"],
                "sections": [
                    {"heading": "Monday Sync", "level": 1},
                    {"heading": "Decisions", "level": 2},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return normalized


def _write_summary(workspace: Path) -> Path:
    return write_daily_summary(
        workspace,
        date=DATE,
        bullets=["Captured sync decisions"],
        highlights=["Team committed to roadmap"],
    )


def _read_yaml(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_facts_generates_microfacts(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _write_normalized(cli_workspace)
    _write_summary(cli_workspace)

    result = cli_runner.invoke(
        app,
        ["ops", "pipeline", "extract-facts", "--date", DATE],
    )

    assert result.exit_code == 0, result.stdout

    facts_path = cli_workspace / "derived" / "microfacts" / f"{DATE}.yaml"
    assert facts_path.exists()

    artifact = _read_yaml(facts_path)
    assert artifact.get("kind") == "microfacts.daily"
    meta = artifact.get("meta", {})
    assert meta.get("model") == "fake-ollama"
    assert meta.get("prompt_path") == "prompts/extract_facts.md"
    assert meta.get("prompt_hash")
    assert meta.get("created_at")
    data = artifact.get("data", {})
    facts = data.get("facts", [])
    assert isinstance(facts, list)
    assert facts
    first_fact = facts[0]
    assert first_fact.get("id") == f"fact-{ENTRY_ID}"
    statement = first_fact.get("statement", "")
    assert "sync notes" in statement.lower()
    assert "section" in statement.lower()
    assert "meta" not in data
    proposals = data.get("claim_proposals", [])
    assert isinstance(proposals, list) and proposals, "Expected claim proposals from micro-facts"
    proposal = proposals[0]
    assert "sync notes" in proposal["statement"].lower()
    assert proposal.get("normalized_ids") == [ENTRY_ID]
    evidence = proposal.get("evidence") or []
    assert any(item.get("entry_id") == ENTRY_ID for item in evidence)
    preview = data.get("preview") or {}
    events = preview.get("claim_events") or []
    assert events, "Expected preview events for micro-facts consolidation"
    event = events[0]
    assert event.get("action") == "upsert"
    assert f"fact-{ENTRY_ID}" in (event.get("claim_id") or "")
    assert "Preview (claim consolidation)" in result.stdout
    assert str(facts_path) in result.stdout


def test_facts_is_idempotent(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _write_normalized(cli_workspace)
    _write_summary(cli_workspace)

    first = cli_runner.invoke(
        app,
        ["ops", "pipeline", "extract-facts", "--date", DATE],
    )
    assert first.exit_code == 0

    facts_path = cli_workspace / "derived" / "microfacts" / f"{DATE}.yaml"
    before = facts_path.stat().st_mtime

    second = cli_runner.invoke(
        app,
        ["ops", "pipeline", "extract-facts", "--date", DATE],
    )
    assert second.exit_code == 0
    after = facts_path.stat().st_mtime

    assert before == after


def test_facts_progress_flag(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _write_normalized(cli_workspace)
    _write_summary(cli_workspace)

    result = cli_runner.invoke(
        app,
        ["ops", "pipeline", "extract-facts", "--date", DATE, "--progress"],
    )

    assert result.exit_code == 0, result.stdout
    assert "Extracting micro-facts" in result.stdout
    assert "[1/1]" in result.stdout


def test_facts_requires_summary(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _write_normalized(cli_workspace)

    result = cli_runner.invoke(
        app,
        ["ops", "pipeline", "extract-facts", "--date", DATE],
    )

    assert result.exit_code != 0
    assert "Daily summary for" in result.stderr
    assert "ops pipeline summarize" in result.stderr
