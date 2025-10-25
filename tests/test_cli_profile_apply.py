"""Tests for `aijournal profile apply` using fake LLM mode."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()
DATE = "2025-02-03"


def _has_profile_apply() -> bool:
    result = runner.invoke(app, ["profile", "apply", "--help"])
    return result.exit_code == 0


@pytest.fixture(autouse=True)
def skip_if_missing() -> None:
    if not _has_profile_apply():
        pytest.skip("profile apply command not available yet")


@pytest.fixture(autouse=True)
def freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "aijournal.cli._now",
        lambda: datetime(2025, 2, 3, 12, 0, tzinfo=UTC),
        raising=False,
    )


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _seed_authoritative(tmp_path: Path) -> None:
    self_profile = {
        "values_motivations": {
            "schwartz_top5": ["Universalism"],
            "last_updated": f"{DATE}T09:00:00Z",
            "review_after_days": 90,
        },
    }
    claims = {
        "claims": [
            {
                "id": "pref_focus",
                "statement": "Focus best before lunch",
                "status": "accepted",
                "confidence": 0.8,
                "evidence": ["entry_a"],
            },
        ],
    }
    _write_yaml(tmp_path / "profile" / "self_profile.yaml", self_profile)
    _write_yaml(tmp_path / "profile" / "claims.yaml", claims)


def _seed_suggestions(tmp_path: Path) -> Path:
    suggestions = {
        "upserts": [
            {
                "target": "claims",
                "operation": "upsert",
                "value": {
                    "id": "pref_evening",
                    "statement": "Prefers evening walks",
                    "status": "tentative",
                    "confidence": 0.6,
                    "evidence": ["entry_b"],
                },
            },
        ],
        "updates": [
            {
                "target": "values_motivations.schwartz_top5",
                "operation": "update",
                "value": ["Universalism", "Benevolence"],
            },
        ],
        "meta": {
            "llm_model": "fake",
            "prompt_path": "prompts/profile_suggest.md",
        },
    }
    path = tmp_path / "derived" / "profile_suggestions" / f"{DATE}.yaml"
    _write_yaml(path, suggestions)
    return path


def _invoke(tmp_path: Path, suggestions_path: Path) -> str:
    env = {"AIJOURNAL_FAKE_OLLAMA": "1"}
    args = [
        "profile",
        "apply",
        "--date",
        DATE,
        "--file",
        str(suggestions_path),
        "--yes",
    ]
    result = runner.invoke(app, args, env=env)
    assert result.exit_code == 0, result.output
    return result.output


def test_profile_apply_merges_suggestions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_authoritative(tmp_path)
    suggestions_path = _seed_suggestions(tmp_path)

    output = _invoke(tmp_path, suggestions_path)
    assert "Applied" in output

    claims = yaml.safe_load((tmp_path / "profile" / "claims.yaml").read_text(encoding="utf-8"))
    new_claims = {claim["id"] for claim in claims["claims"]}
    assert "pref_evening" in new_claims
    assert len(claims["claims"]) == len(new_claims), "Duplicate claim IDs"

    profile = yaml.safe_load(
        (tmp_path / "profile" / "self_profile.yaml").read_text(encoding="utf-8"),
    )
    assert profile["values_motivations"]["schwartz_top5"] == [
        "Universalism",
        "Benevolence",
    ]


def test_profile_apply_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_authoritative(tmp_path)
    suggestions_path = _seed_suggestions(tmp_path)

    first_output = _invoke(tmp_path, suggestions_path)
    claims_after_first = (tmp_path / "profile" / "claims.yaml").read_text(encoding="utf-8")
    profile_after_first = (tmp_path / "profile" / "self_profile.yaml").read_text(encoding="utf-8")

    second_output = _invoke(tmp_path, suggestions_path)

    assert (tmp_path / "profile" / "claims.yaml").read_text(encoding="utf-8") == claims_after_first
    assert (tmp_path / "profile" / "self_profile.yaml").read_text(
        encoding="utf-8",
    ) == profile_after_first
    assert "Applied" in first_output
    assert "No changes" in second_output or second_output == first_output
