"""Tests for `aijournal interview` CLI (tests only)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from aijournal.cli import app
from aijournal.domain.persona import InterviewQuestion, InterviewSet
from aijournal.io.yaml_io import dump_yaml
from tests.helpers import write_daily_summary

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner

DATE = "2025-02-03"


@pytest.fixture(autouse=True)
def fake_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def _seed_profile(tmp_path) -> None:
    profile = {
        "traits": {
            "big_five": {
                "openness": {"score": 0.7, "last_updated": "2024-01-01"},
                "conscientiousness": {"score": 0.4, "last_updated": "2022-01-01"},
            },
        },
    }
    _write(tmp_path / "profile" / "self_profile.yaml", dump_yaml(profile))
    claims = {
        "claims": [
            {
                "id": "claim_a",
                "statement": "Needs morning focus",
                "last_updated": "2023-01-01",
            },
        ],
    }
    _write(tmp_path / "profile" / "claims.yaml", dump_yaml(claims))


def _seed_normalized(tmp_path) -> None:
    entry = {
        "id": "entry",
        "created_at": f"{DATE}T09:00:00Z",
        "source_path": f"data/journal/{DATE}-entry.md",
        "title": "Daily Notes",
        "sections": [],
    }
    _write(
        tmp_path / "data" / "normalized" / DATE / "entry.yaml",
        dump_yaml(entry),
    )


def _seed_summary(tmp_path: Path, *, date: str = DATE, **kwargs) -> None:
    write_daily_summary(
        tmp_path,
        date=date,
        bullets=kwargs.get("bullets", ["Synced priorities"]),
        highlights=kwargs.get("highlights", ["Team alignment"]),
    )


def test_interview_emits_ranked_probes(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _seed_normalized(cli_workspace)
    _seed_summary(cli_workspace)

    result = cli_runner.invoke(app, ["ops", "profile", "interview", "--date", DATE])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    probes = [line for line in lines if line.startswith("- ")]
    assert 2 <= len(probes) <= 4
    assert any("traits.big_five.conscientiousness" in line for line in probes)
    assert any("claim_a" in line for line in probes)


def test_interview_fallback_when_no_stale(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    fresh_profile = {"traits": {"big_five": {"openness": {"last_updated": DATE}}}}
    _write(cli_workspace / "profile" / "self_profile.yaml", dump_yaml(fresh_profile))
    _write(cli_workspace / "profile" / "claims.yaml", dump_yaml({"claims": []}))
    _seed_normalized(cli_workspace)
    _seed_summary(cli_workspace)

    result = cli_runner.invoke(app, ["ops", "profile", "interview", "--date", DATE])
    assert result.exit_code == 0
    probes = [line for line in result.output.splitlines() if line.startswith("- ")]
    assert len(probes) == 3


def test_interview_requires_summary(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _seed_normalized(cli_workspace)

    result = cli_runner.invoke(app, ["ops", "profile", "interview", "--date", DATE])

    assert result.exit_code != 0
    assert "Daily summary for" in result.stderr


def test_interview_missing_profile(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_normalized(cli_workspace)
    for rel in ("profile/self_profile.yaml", "profile/claims.yaml"):
        target = cli_workspace / rel
        if target.exists():
            target.unlink()

    result = cli_runner.invoke(app, ["ops", "profile", "interview", "--date", DATE])
    assert result.exit_code != 0
    assert "No profile data" in result.output


def test_interview_missing_entries(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)

    result = cli_runner.invoke(app, ["ops", "profile", "interview", "--date", DATE])
    assert result.exit_code != 0
    assert "No normalized entries" in result.output


def test_interview_live_mode_structured(
    cli_workspace: Path,
    cli_runner: CliRunner,
    monkeypatch,
) -> None:  # type: ignore[name-defined]
    _seed_profile(cli_workspace)
    _seed_normalized(cli_workspace)
    _seed_summary(cli_workspace)
    _seed_summary(
        cli_workspace,
        date="2025-02-02",
        bullets=["Captured travel blockers"],
    )
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "0")

    captured_blocks: dict[str, str] = {}

    def _fake_structured(_prompt_path, prompt_vars, **_kwargs) -> InterviewSet:
        captured_blocks.update(prompt_vars)
        return InterviewSet(
            questions=[
                InterviewQuestion(
                    id="focus-check",
                    text="What changed about morning focus routines?",
                    target_facet="claim:claim_a",
                    priority="high",
                ),
            ],
        )

    monkeypatch.setattr(
        "aijournal.services.ollama.invoke_structured_llm",
        lambda *a, **k: _fake_structured(*a, **k),
    )

    result = cli_runner.invoke(
        app,
        ["ops", "profile", "interview", "--date", DATE],
        env={"AIJOURNAL_FAKE_OLLAMA": "0"},
    )
    assert result.exit_code == 0, result.output
    assert "focus routines" in result.output
    summary_payload = json.loads(captured_blocks.get("summary_json", "{}"))
    assert summary_payload.get("bullets"), "Expected summary payload to be present"
    window_payload = json.loads(captured_blocks.get("summary_window_json", "[]"))
    assert any(item.get("day") == "2025-02-02" for item in window_payload)
