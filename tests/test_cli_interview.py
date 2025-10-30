"""Tests for `aijournal interview` CLI (tests only)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app
from aijournal.domain.persona import InterviewQuestion, InterviewSet

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
    _write(tmp_path / "profile" / "self_profile.yaml", yaml.safe_dump(profile))
    claims = {
        "claims": [
            {
                "id": "claim_a",
                "statement": "Needs morning focus",
                "last_updated": "2023-01-01",
            },
        ],
    }
    _write(tmp_path / "profile" / "claims.yaml", yaml.safe_dump(claims))


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
        yaml.safe_dump(entry),
    )


def test_interview_emits_ranked_probes(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _seed_normalized(cli_workspace)

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
    _write(cli_workspace / "profile" / "self_profile.yaml", yaml.safe_dump(fresh_profile))
    _write(cli_workspace / "profile" / "claims.yaml", yaml.safe_dump({"claims": []}))
    _seed_normalized(cli_workspace)

    result = cli_runner.invoke(app, ["ops", "profile", "interview", "--date", DATE])
    assert result.exit_code == 0
    probes = [line for line in result.output.splitlines() if line.startswith("- ")]
    assert len(probes) == 3


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
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "0")

    def _fake_structured(*_args, **_kwargs) -> InterviewSet:
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

    monkeypatch.setattr("aijournal.cli._invoke_structured_llm", lambda *a, **k: _fake_structured())

    result = cli_runner.invoke(
        app,
        ["ops", "profile", "interview", "--date", DATE],
        env={"AIJOURNAL_FAKE_OLLAMA": "0"},
    )
    assert result.exit_code == 0, result.output
    assert "focus routines" in result.output
