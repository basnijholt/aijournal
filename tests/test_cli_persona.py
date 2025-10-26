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
    source_mtimes = payload["meta"].get("source_mtimes", {})
    assert "profile/self_profile.yaml" in source_mtimes
    assert "profile/claims.yaml" in source_mtimes


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


def test_persona_build_handles_empty_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0, init_result.stdout
    claims_path = tmp_path / "profile" / "claims.yaml"
    claims_path.write_text("claims: []\n", encoding="utf-8")

    result = runner.invoke(app, ["persona", "build"])
    assert result.exit_code == 0, result.stdout
    payload = yaml.safe_load(
        (tmp_path / "derived" / "persona" / "persona_core.yaml").read_text(encoding="utf-8"),
    )
    assert payload["persona"]["claims"] == []
    assert payload["persona"]["profile"], "profile slice should be included when available"


def test_persona_build_respects_min_claims(
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
            "1",
            "--max-claims",
            "3",
            "--min-claims",
            "2",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = yaml.safe_load(
        (tmp_path / "derived" / "persona" / "persona_core.yaml").read_text(encoding="utf-8"),
    )
    assert payload["meta"]["claim_count"] == 2
    assert payload["meta"].get("budget_exceeded") is True


def test_persona_status_reports_fresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0, init_result.stdout
    _seed_claims(tmp_path)
    build_result = runner.invoke(app, ["persona", "build"])
    assert build_result.exit_code == 0, build_result.stdout

    status_result = runner.invoke(app, ["persona", "status"])
    assert status_result.exit_code == 0, status_result.output
    assert "up to date" in status_result.output.lower()


def test_persona_status_detects_stale_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0, init_result.stdout
    _seed_claims(tmp_path)
    build_result = runner.invoke(app, ["persona", "build"])
    assert build_result.exit_code == 0, build_result.stdout

    claims_path = tmp_path / "profile" / "claims.yaml"
    claims_payload = yaml.safe_load(claims_path.read_text(encoding="utf-8"))
    claims_payload["claims"].append(
        make_claim_atom(
            "pref.new",
            "Evening walks reduce stress",
            strength=0.51,
            last_updated="2025-02-05T19:00:00Z",
        ),
    )
    claims_path.write_text(yaml.safe_dump(claims_payload, sort_keys=False), encoding="utf-8")

    status_result = runner.invoke(app, ["persona", "status"])
    assert status_result.exit_code != 0
    assert "claims.yaml" in status_result.output
