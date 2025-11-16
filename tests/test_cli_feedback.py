"""Tests for the feedback apply command."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from aijournal.cli import app
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.events import FeedbackAdjustmentEvent, FeedbackBatch
from aijournal.io.artifacts import save_artifact
from aijournal.io.yaml_io import dump_yaml
from tests.helpers import make_claim_atom

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner


@pytest.fixture(autouse=True)
def _fake_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")


def _write_claims(path: Path, *, claim_id: str, strength: float) -> None:
    payload = {"claims": [make_claim_atom(claim_id, "Focus work", strength=strength)]}
    path.write_text(dump_yaml(payload, sort_keys=False), encoding="utf-8")


def _write_feedback_batch(
    path: Path,
    *,
    claim_id: str,
    old_strength: float,
    new_strength: float,
) -> None:
    created_at = "2025-10-27T17:30:48Z"
    batch = FeedbackBatch(
        batch_id="test-batch",
        created_at=created_at,
        session_id="session-1",
        question="What progress did I make?",
        feedback="down" if new_strength < old_strength else "up",
        events=[
            FeedbackAdjustmentEvent(
                claim_id=claim_id,
                old_strength=old_strength,
                new_strength=new_strength,
                delta=new_strength - old_strength,
            ),
        ],
    )
    save_artifact(
        path,
        Artifact[FeedbackBatch](
            kind=ArtifactKind.FEEDBACK_BATCH,
            meta=ArtifactMeta(created_at=created_at),
            data=batch,
        ),
    )


def test_feedback_apply_updates_claims_and_archives(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    claims_path = cli_workspace / "profile" / "claims.yaml"
    _write_claims(claims_path, claim_id="focus-claim", strength=0.5)

    pending_dir = cli_workspace / "derived" / "pending" / "profile_updates"
    pending_dir.mkdir(parents=True, exist_ok=True)
    batch_path = pending_dir / "feedback_focus.yaml"
    _write_feedback_batch(
        batch_path,
        claim_id="focus-claim",
        old_strength=0.5,
        new_strength=0.45,
    )

    result = cli_runner.invoke(app, ["ops", "feedback", "apply"])
    assert result.exit_code == 0, result.stdout
    output = result.stdout or result.output
    assert "Applied 1 feedback adjustment" in output
    claims = yaml.safe_load(claims_path.read_text(encoding="utf-8"))
    assert pytest.approx(claims["claims"][0]["strength"], rel=1e-4) == 0.45

    archive_dir = pending_dir / "applied_feedback"
    archived = list(archive_dir.glob("feedback_focus*.yaml"))
    assert len(archived) == 1


def test_feedback_apply_no_batches_exits_non_zero(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    pending_dir = cli_workspace / "derived" / "pending" / "profile_updates"
    pending_dir.mkdir(parents=True, exist_ok=True)
    _write_claims(cli_workspace / "profile" / "claims.yaml", claim_id="focus-claim", strength=0.5)

    result = cli_runner.invoke(app, ["ops", "feedback", "apply"])
    assert result.exit_code != 0
    assert "No feedback batches to apply." in (result.stderr or result.stdout or "")
