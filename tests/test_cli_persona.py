"""Tests for `aijournal persona build`."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from aijournal.cli import app
from aijournal.io.yaml_io import dump_yaml
from tests.helpers import make_claim_atom

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner


def _seed_claims(workspace: Path) -> None:
    path = workspace / "profile" / "claims.yaml"
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
    path.write_text(dump_yaml(payload, sort_keys=False), encoding="utf-8")


def test_persona_build_generates_core(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_claims(cli_workspace)

    result = cli_runner.invoke(app, ["ops", "persona", "build"])
    assert result.exit_code == 0, result.stdout

    persona_path = cli_workspace / "derived" / "persona" / "persona_core.yaml"
    assert persona_path.exists()
    artifact = yaml.safe_load(persona_path.read_text(encoding="utf-8"))
    assert artifact.get("kind") == "persona.core"
    data = artifact.get("data", {})
    meta = artifact.get("meta", {})
    notes = meta.get("notes", {}) or {}
    persona_claims = data.get("claims", [])
    assert persona_claims, "claims should be present"
    assert notes.get("claim_count") == str(len(persona_claims))
    assert int(notes.get("planned_tokens", "0")) > 0
    source_mtimes = json.loads(notes.get("source_mtimes", "{}"))
    assert "profile/self_profile.yaml" in source_mtimes
    assert "profile/claims.yaml" in source_mtimes


def test_persona_build_trims_when_budget_forced(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_claims(cli_workspace)

    result = cli_runner.invoke(
        app,
        [
            "ops",
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

    persona_path = cli_workspace / "derived" / "persona" / "persona_core.yaml"
    artifact = yaml.safe_load(persona_path.read_text(encoding="utf-8"))
    meta = artifact.get("meta", {})
    notes = meta.get("notes", {}) or {}
    trimmed = json.loads(notes.get("trimmed", "[]"))
    assert trimmed, "expect at least one trimmed claim when forcing small budget"
    trimmed_ids = [item.get("id") for item in trimmed]
    assert "pref.evening" in trimmed_ids
    assert json.loads(notes.get("budget_exceeded", "false")) is True


def test_persona_build_handles_empty_claims(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    claims_path = cli_workspace / "profile" / "claims.yaml"
    claims_path.write_text("claims: []\n", encoding="utf-8")

    result = cli_runner.invoke(app, ["ops", "persona", "build"])
    assert result.exit_code == 0, result.stdout
    artifact = yaml.safe_load(
        (cli_workspace / "derived" / "persona" / "persona_core.yaml").read_text(encoding="utf-8"),
    )
    persona_data = artifact.get("data", {})
    assert persona_data.get("claims") == []
    assert persona_data.get("profile"), "profile slice should be included when available"


def test_persona_build_respects_min_claims(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_claims(cli_workspace)

    result = cli_runner.invoke(
        app,
        [
            "ops",
            "persona",
            "build",
            "--token-budget",
            "1",
            "--max-claims",
            "3",
            "--min-claims",
            "2",
        ],
    )
    assert result.exit_code == 0, result.stdout
    artifact = yaml.safe_load(
        (cli_workspace / "derived" / "persona" / "persona_core.yaml").read_text(encoding="utf-8"),
    )
    notes = artifact.get("meta", {}).get("notes", {}) or {}
    assert notes.get("claim_count") == "2"
    assert json.loads(notes.get("budget_exceeded", "false")) is True


def test_persona_status_reports_fresh(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_claims(cli_workspace)
    build_result = cli_runner.invoke(app, ["ops", "persona", "build"])
    assert build_result.exit_code == 0, build_result.stdout

    status_result = cli_runner.invoke(app, ["ops", "persona", "status"])
    assert status_result.exit_code == 0, status_result.output
    assert "up to date" in status_result.output.lower()


def test_persona_status_detects_stale_profile(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_claims(cli_workspace)
    build_result = cli_runner.invoke(app, ["ops", "persona", "build"])
    assert build_result.exit_code == 0, build_result.stdout

    claims_path = cli_workspace / "profile" / "claims.yaml"
    claims_payload = yaml.safe_load(claims_path.read_text(encoding="utf-8"))
    claims_payload["claims"].append(
        make_claim_atom(
            "pref.new",
            "Evening walks reduce stress",
            strength=0.51,
            last_updated="2025-02-05T19:00:00Z",
        ),
    )
    claims_path.write_text(dump_yaml(claims_payload, sort_keys=False), encoding="utf-8")

    status_result = cli_runner.invoke(app, ["ops", "persona", "status"])
    assert status_result.exit_code != 0
    assert "claims.yaml" in status_result.output
