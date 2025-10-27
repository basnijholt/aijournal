"""Tests for `aijournal advise` (fake LLM mode)."""

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


def _has_command(name: str) -> bool:
    return any(info.name == name for info in app.registered_commands)


@pytest.fixture(autouse=True)
def skip_if_missing() -> None:
    if not _has_command("advise"):
        pytest.skip("advise command not available yet")


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def _seed_profile(workspace: Path) -> None:
    self_profile = """
coaching_prefs:
  tone: "direct, warm"
  depth: "concrete first"
boundaries_ethics:
  red_lines:
    - "No health advice"
"""
    claims = yaml.safe_dump(
        {
            "claims": [
                make_claim_atom(
                    "pref_focus",
                    "Focus best before lunch",
                    strength=0.8,
                    status="accepted",
                    last_updated=f"{DATE}T08:00:00Z",
                ),
            ],
        },
        sort_keys=False,
    )
    _write_yaml(workspace / "profile" / "self_profile.yaml", self_profile)
    _write_yaml(workspace / "profile" / "claims.yaml", claims)


def _seed_pending_prompt(workspace: Path) -> None:
    payload = {
        "preview": {
            "interview_prompts": ["Where do morning routines break down during travel weeks?"],
        }
    }
    path = workspace / "derived" / "pending" / "profile_updates" / "pending.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _invoke(workspace: Path) -> tuple[dict[str, object], Path, int]:
    result = runner.invoke(app, ["advise", "How to plan next week?"])
    assert result.exit_code == 0, result.output
    folder = workspace / "derived" / "advice" / DATE
    files = sorted(folder.glob("*.yaml"))
    assert files, "No advice file generated"
    data = yaml.safe_load(files[0].read_text(encoding="utf-8"))
    return data, files[0], len(files)


def test_advise_generates_advice(cli_workspace: Path) -> None:
    _seed_profile(cli_workspace)
    _seed_pending_prompt(cli_workspace)

    data, advice_file, _count = _invoke(cli_workspace)

    assert isinstance(data.get("recommendations"), list)
    assert data.get("alignment")
    assumptions = data.get("assumptions") or []
    assert assumptions == ["Reference claim: Focus best before lunch"]
    steps = data.get("recommendations", [{}])[0].get("steps") or []
    assert steps[0] == "Protect two deep-work mornings for focused execution."
    assert steps[1] == "Question under review: How to plan next week?"
    assert steps[-1] == (
        "Journal on pending prompt: Where do morning routines break down during travel weeks?"
    )
    meta = data.get("meta", {})
    for key in ("llm_model", "prompt_path", "prompt_hash", "created_at"):
        assert meta.get(key)


def test_advise_is_idempotent(cli_workspace: Path) -> None:
    _seed_profile(cli_workspace)
    _seed_pending_prompt(cli_workspace)

    data1, advice_file, count1 = _invoke(cli_workspace)
    before = advice_file.stat().st_mtime

    data2, advice_file_again, count2 = _invoke(cli_workspace)
    assert advice_file_again == advice_file
    assert count1 == count2
    assert advice_file_again.stat().st_mtime == before
    assert data1["recommendations"][0]["steps"] == data2["recommendations"][0]["steps"]
