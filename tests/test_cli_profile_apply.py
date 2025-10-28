"""Tests for `aijournal profile apply` using fake LLM mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app
from tests.helpers import make_claim_atom

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


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _seed_authoritative(workspace: Path) -> None:
    self_profile = {
        "values_motivations": {
            "schwartz_top5": ["Universalism"],
            "last_updated": f"{DATE}T09:00:00Z",
            "review_after_days": 90,
        },
    }
    claims = {
        "claims": [
            make_claim_atom(
                "pref_focus",
                "Focus best before lunch",
                strength=0.82,
                status="accepted",
                last_updated=f"{DATE}T09:00:00Z",
            ),
        ],
    }
    _write_yaml(workspace / "profile" / "self_profile.yaml", self_profile)
    _write_yaml(workspace / "profile" / "claims.yaml", claims)


def _seed_suggestions(workspace: Path) -> Path:
    suggestions = {
        "upserts": [
            {
                "target": "claims",
                "operation": "upsert",
                "value": make_claim_atom(
                    "pref_evening",
                    "Prefers evening walks",
                    strength=0.6,
                    status="tentative",
                    method="inferred",
                    last_updated=f"{DATE}T10:00:00Z",
                ),
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
    path = workspace / "derived" / "profile_suggestions" / f"{DATE}.yaml"
    _write_yaml(path, suggestions)
    return path


def _invoke(suggestions_path: Path) -> str:
    args = [
        "profile",
        "apply",
        "--date",
        DATE,
        "--file",
        str(suggestions_path),
        "--yes",
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result.output


def test_profile_apply_merges_suggestions(cli_workspace: Path) -> None:
    _seed_authoritative(cli_workspace)
    suggestions_path = _seed_suggestions(cli_workspace)

    output = _invoke(suggestions_path)
    assert "Applied" in output

    claims = yaml.safe_load((cli_workspace / "profile" / "claims.yaml").read_text(encoding="utf-8"))
    new_claims = {claim["id"] for claim in claims["claims"]}
    assert "pref_evening" in new_claims
    assert len(claims["claims"]) == len(new_claims), "Duplicate claim IDs"

    profile = yaml.safe_load(
        (cli_workspace / "profile" / "self_profile.yaml").read_text(encoding="utf-8"),
    )
    assert profile["values_motivations"]["schwartz_top5"] == [
        "Universalism",
        "Benevolence",
    ]


def test_profile_apply_idempotent(cli_workspace: Path) -> None:
    _seed_authoritative(cli_workspace)
    suggestions_path = _seed_suggestions(cli_workspace)

    first_output = _invoke(suggestions_path)
    claims_after_first = (cli_workspace / "profile" / "claims.yaml").read_text(encoding="utf-8")
    profile_after_first = (cli_workspace / "profile" / "self_profile.yaml").read_text(
        encoding="utf-8"
    )

    second_output = _invoke(suggestions_path)

    assert (cli_workspace / "profile" / "claims.yaml").read_text(
        encoding="utf-8"
    ) == claims_after_first
    assert (cli_workspace / "profile" / "self_profile.yaml").read_text(
        encoding="utf-8",
    ) == profile_after_first
    assert "Applied" in first_output
    assert "No changes" in second_output or second_output == first_output
