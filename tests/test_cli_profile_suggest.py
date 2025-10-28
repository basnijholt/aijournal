"""Tests for `aijournal profile suggest` using fake LLM mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app
from tests.helpers import make_claim_atom

if TYPE_CHECKING:
    from pathlib import Path

DATE = "2025-02-03"
ENTRY_ID = "2025-02-03-sync-notes"


def _has_command() -> bool:
    # Typer adds nested commands as TyperCommand objects registered via add_typer
    return any(cmd.name == "profile" for cmd in app.registered_commands)


@pytest.fixture(autouse=True)
def skip_if_missing() -> None:
    if not _has_command():
        pytest.skip("profile commands not available yet")


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _seed_normalized(workspace: Path) -> None:
    normalized = {
        "id": ENTRY_ID,
        "created_at": f"{DATE}T09:00:00Z",
        "source_path": f"data/journal/2025/02/03/{ENTRY_ID}.md",
        "title": "Sync Notes",
        "tags": ["team"],
        "sections": [
            {"heading": "Monday Sync", "level": 1},
            {"heading": "Decisions", "level": 2},
        ],
    }
    _write_yaml(workspace / "data" / "normalized" / DATE / f"{ENTRY_ID}.yaml", normalized)


def _seed_profile(workspace: Path) -> None:
    self_profile = {
        "values_motivations": {
            "schwartz_top5": ["Universalism"],
            "last_updated": f"{DATE}T07:00:00Z",
            "review_after_days": 30,
        },
    }
    claims = {
        "claims": [
            make_claim_atom(
                "pref_focus",
                "Focus best before lunch",
                strength=0.8,
                status="accepted",
                last_updated=f"{DATE}T08:00:00Z",
            ),
        ],
    }
    _write_yaml(workspace / "profile" / "self_profile.yaml", self_profile)
    _write_yaml(workspace / "profile" / "claims.yaml", claims)


def _invoke(
    workspace: Path,
    cli_runner: CliRunner,
    extra_args: list[str] | None = None,
) -> tuple[str, Path, int]:
    args = ["profile", "suggest", "--date", DATE]
    if extra_args:
        args.extend(extra_args)
    result = cli_runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    path = workspace / "derived" / "profile_suggestions" / f"{DATE}.yaml"
    assert path.exists()
    folder = path.parent
    count = len(list(folder.glob("*.yaml")))
    return result.output, path, count


def test_profile_suggest_writes_suggestions(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_normalized(cli_workspace)
    _seed_profile(cli_workspace)

    _, suggestions_path, _ = _invoke(cli_workspace, cli_runner)

    data = yaml.safe_load(suggestions_path.read_text(encoding="utf-8"))
    assert data.get("upserts") or data.get("updates"), "Expected suggested changes"
    meta = data.get("meta", {})
    for key in ("llm_model", "prompt_path", "prompt_hash", "created_at"):
        assert meta.get(key)
    assert meta.get("llm_model") == "fake-ollama"


def test_profile_suggest_is_idempotent(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_normalized(cli_workspace)
    _seed_profile(cli_workspace)

    _, suggestions_path, count_before = _invoke(cli_workspace, cli_runner)
    mtime_before = suggestions_path.stat().st_mtime

    _, suggestions_path_again, count_after = _invoke(cli_workspace, cli_runner)

    assert suggestions_path_again == suggestions_path
    assert count_before == count_after
    assert suggestions_path_again.stat().st_mtime == mtime_before


def test_profile_suggest_progress_flag(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_normalized(cli_workspace)
    _seed_profile(cli_workspace)

    output, _, _ = _invoke(cli_workspace, cli_runner, ["--progress"])

    assert "Generating profile suggestions" in output
    assert "[1/1]" in output
