"""Tests for `aijournal interview` CLI (tests only)."""

from __future__ import annotations

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app

runner = CliRunner()
DATE = "2025-02-03"


def _has_interview_command() -> bool:
    return any(cmd.name == "interview" for cmd in app.registered_commands)


@pytest.fixture(autouse=True)
def skip_if_missing() -> None:
    if not _has_interview_command():
        pytest.skip("interview command not available yet")


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
        "title": "Daily Notes",
        "sections": [],
    }
    _write(
        tmp_path / "data" / "normalized" / DATE / "entry.yaml",
        yaml.safe_dump(entry),
    )


def test_interview_emits_ranked_probes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    _seed_normalized(tmp_path)

    result = runner.invoke(app, ["interview", "--date", DATE])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    probes = [line for line in lines if line.startswith("- ")]
    assert 2 <= len(probes) <= 4
    assert any("traits.big_five.conscientiousness" in line for line in probes)
    assert any("claim_a" in line for line in probes)


def test_interview_fallback_when_no_stale(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    fresh_profile = {"traits": {"big_five": {"openness": {"last_updated": DATE}}}}
    _write(tmp_path / "profile" / "self_profile.yaml", yaml.safe_dump(fresh_profile))
    _write(tmp_path / "profile" / "claims.yaml", yaml.safe_dump({"claims": []}))
    _seed_normalized(tmp_path)

    result = runner.invoke(app, ["interview", "--date", DATE])
    assert result.exit_code == 0
    probes = [line for line in result.output.splitlines() if line.startswith("- ")]
    assert len(probes) == 8


def test_interview_missing_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_normalized(tmp_path)

    result = runner.invoke(app, ["interview", "--date", DATE])
    assert result.exit_code != 0
    assert "No profile data" in result.output


def test_interview_missing_entries(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)

    result = runner.invoke(app, ["interview", "--date", DATE])
    assert result.exit_code != 0
    assert "No normalized entries" in result.output
