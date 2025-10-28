"""Tests for the feedback apply command."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app
from tests.helpers import make_claim_atom


@pytest.fixture(autouse=True)
def _fake_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")


def _write_claims(path: Path, *, claim_id: str, strength: float) -> None:
    payload = {"claims": [make_claim_atom(claim_id, "Focus work", strength=strength)]}
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _write_feedback_batch(path: Path, *, claim_id: str, delta: float, new_strength: float) -> None:
    payload = {
        "kind": "chat_feedback",
        "session_id": "session-1",
        "timestamp": "2025-10-27T17:30:48Z",
        "question": "What progress did I make?",
        "feedback": "down" if delta < 0 else "up",
        "claim_adjustments": [
            {
                "id": claim_id,
                "delta": delta,
                "new_strength": new_strength,
            }
        ],
        "claim_markers": [claim_id],
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_feedback_apply_updates_claims_and_archives(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    claims_path = cli_workspace / "profile" / "claims.yaml"
    _write_claims(claims_path, claim_id="focus-claim", strength=0.5)

    pending_dir = cli_workspace / "derived" / "pending" / "profile_updates"
    pending_dir.mkdir(parents=True, exist_ok=True)
    batch_path = pending_dir / "feedback_focus.yaml"
    _write_feedback_batch(batch_path, claim_id="focus-claim", delta=-0.05, new_strength=0.45)

    result = cli_runner.invoke(app, ["feedback-apply"])
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

    result = cli_runner.invoke(app, ["feedback-apply"])
    assert result.exit_code != 0
    assert "No feedback batches to apply." in (result.stderr or result.stdout or "")
