"""Tests for `aijournal advise` (fake LLM mode)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from aijournal.cli import app
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.changes import ProfileUpdateProposals
from aijournal.io.artifacts import load_artifact, save_artifact
from aijournal.io.yaml_io import dump_yaml
from aijournal.models.derived import (
    AdviceCard,
    ProfileUpdateBatch,
    ProfileUpdatePreview,
)
from tests.helpers import make_claim_atom

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner

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
    claims = dump_yaml(
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
    batch = ProfileUpdateBatch(
        batch_id="pending-batch",
        created_at=f"{DATE}T00:00:00Z",
        date=DATE,
        proposals=ProfileUpdateProposals(),
        preview=ProfileUpdatePreview(
            interview_prompts=["Where do morning routines break down during travel weeks?"],
        ),
    )
    path = workspace / "derived" / "pending" / "profile_updates" / "pending.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    save_artifact(
        path,
        Artifact[ProfileUpdateBatch](
            kind=ArtifactKind.PROFILE_UPDATES,
            meta=ArtifactMeta(created_at=f"{DATE}T00:00:00Z"),
            data=batch,
        ),
    )


def _invoke(
    workspace: Path,
    cli_runner: CliRunner,
) -> tuple[ArtifactKind, AdviceCard, Path, int]:
    result = cli_runner.invoke(app, ["advise", "How to plan next week?"])
    assert result.exit_code == 0, result.output
    folder = workspace / "derived" / "advice" / DATE
    files = sorted(folder.glob("*.yaml"))
    assert files, "No advice file generated"
    artifact = load_artifact(files[0], AdviceCard)
    return artifact.kind, artifact.data, artifact.meta, files[0], len(files)


def test_advise_generates_advice(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _seed_pending_prompt(cli_workspace)

    kind, card, meta, _advice_file, _count = _invoke(cli_workspace, cli_runner)

    assert kind is ArtifactKind.ADVICE_CARD

    assert card.recommendations
    assert card.alignment
    assumptions = card.assumptions or []
    assert any("Focus best before lunch" in str(item) for item in assumptions)
    steps = card.recommendations[0].steps if card.recommendations else []
    assert len(steps) >= 2
    assert "deep-work" in steps[0]
    assert "How to plan next week" in steps[1]
    assert any("morning routines" in step and "travel weeks" in step for step in steps)
    assert meta.model
    assert meta.prompt_path
    assert meta.prompt_hash
    assert meta.created_at


def test_advise_is_idempotent(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _seed_pending_prompt(cli_workspace)

    kind1, data1, _meta1, advice_file, count1 = _invoke(cli_workspace, cli_runner)
    before = advice_file.stat().st_mtime

    kind2, data2, _meta2, advice_file_again, count2 = _invoke(cli_workspace, cli_runner)
    assert kind1 is ArtifactKind.ADVICE_CARD
    assert kind2 is ArtifactKind.ADVICE_CARD
    assert advice_file_again == advice_file
    assert count1 == count2
    assert advice_file_again.stat().st_mtime == before
    assert data1.recommendations[0].steps == data2.recommendations[0].steps
