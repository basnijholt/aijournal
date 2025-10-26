"""Tests for `aijournal persona build`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from typer.testing import CliRunner

from aijournal.cli import app
from tests.helpers import make_claim_atom

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

runner = CliRunner()


def _seed_claims(tmp_path: Path) -> None:
    path = tmp_path / "profile" / "claims.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "claims": [
            make_claim_atom(
                "pref.morning",
                "Morning focus window",
                subject="focus",
                predicate="best_window",
                value="08:00-11:00",
                strength=0.92,
                status="accepted",
                last_updated="2025-02-01T09:00:00Z",
            ),
            make_claim_atom(
                "pref.evening",
                "Evenings are for writing",
                subject="writing",
                predicate="best_window",
                value="20:00-22:00",
                strength=0.55,
                status="tentative",
                last_updated="2025-01-28T21:00:00Z",
            ),
            make_claim_atom(
                "pref.weekend",
                "Weekend review cadence",
                subject="reflection",
                predicate="cadence",
                value="weekend",
                strength=0.61,
                status="accepted",
                last_updated="2025-01-15T12:00:00Z",
            ),
        ],
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_persona_build_generates_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0, init_result.stdout
    _seed_claims(tmp_path)

    result = runner.invoke(app, ["persona", "build"])
    assert result.exit_code == 0, result.stdout

    persona_path = tmp_path / "derived" / "persona" / "persona_core.yaml"
    assert persona_path.exists()
    payload = yaml.safe_load(persona_path.read_text(encoding="utf-8"))
    assert payload["persona"]["claims"], "claims should be present"
    assert payload["meta"]["claim_count"] == len(payload["persona"]["claims"])
    assert payload["meta"]["planned_tokens"] > 0


def test_persona_build_trims_when_budget_forced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0, init_result.stdout
    _seed_claims(tmp_path)

    result = runner.invoke(
        app,
        [
            "persona",
            "build",
            "--token-budget",
            "10",
            "--max-claims",
            "3",
            "--min-claims",
            "0",
        ],
    )
    assert result.exit_code == 0, result.stdout

    persona_path = tmp_path / "derived" / "persona" / "persona_core.yaml"
    payload = yaml.safe_load(persona_path.read_text(encoding="utf-8"))
    trimmed = payload["meta"].get("trimmed", [])
    assert trimmed, "expect at least one trimmed claim when forcing small budget"
    trimmed_ids = [item["id"] for item in trimmed]
    assert "pref.evening" in trimmed_ids
    assert isinstance(payload["meta"].get("budget_exceeded"), bool)
