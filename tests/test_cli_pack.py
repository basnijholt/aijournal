"""Tests for `aijournal pack` CLI."""

from __future__ import annotations

import json
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
ENTRY_ID = "2025-02-03-sync-notes"
PRIOR_DATE = "2025-02-02"
PRIOR_ENTRY_ID = f"{PRIOR_DATE}-retro-notes"
ADVICE_QUESTION = "How do I protect deep work blocks?"


def _has_pack_command() -> bool:
    return any(cmd.name == "pack" for cmd in app.registered_commands)


@pytest.fixture(autouse=True)
def skip_if_missing() -> None:
    if not _has_pack_command():
        pytest.skip("pack command not available yet")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def _seed_profile(tmp_path: Path) -> None:
    _write(
        tmp_path / "profile" / "self_profile.yaml",
        """
traits:
  big_five:
    openness: {score: 0.7}
""",
    )
    claims_payload = {
        "claims": [
            make_claim_atom(
                "pref_focus",
                "Focus best before lunch",
                strength=0.78,
                status="accepted",
                last_updated=f"{DATE}T08:00:00Z",
            ),
        ],
    }
    _write(
        tmp_path / "profile" / "claims.yaml",
        yaml.safe_dump(claims_payload, sort_keys=False),
    )


def _seed_daily_artifacts(
    tmp_path: Path,
    day: str = DATE,
    entry_id: str | None = None,
) -> str:
    entry_value = entry_id or (ENTRY_ID if day == DATE else f"{day}-entry")
    _write(
        tmp_path / "data" / "normalized" / day / f"{entry_value}.yaml",
        f"id: {entry_value}\ncreated_at: {day}T09:00:00Z\ntitle: Sync Notes",
    )
    _write(
        tmp_path / "derived" / "summaries" / f"{day}.yaml",
        f"day: {day}\nbullets:\n  - planning",
    )
    _write(
        tmp_path / "derived" / "microfacts" / f"{day}.yaml",
        "facts:\n  - id: fact1\n    statement: Prefers mornings",
    )
    return entry_value


def _seed_advice(tmp_path: Path, day: str = DATE, question: str = ADVICE_QUESTION) -> Path:
    slug = "-".join(part for part in question.lower().split())
    advice_path = tmp_path / "derived" / "advice" / day / f"{slug}.yaml"
    payload = {
        "question": question,
        "recommendations": [
            {
                "title": "Protect maker time",
                "actions": [
                    "Hold a 90-minute deep-work block",
                    "Push non-urgent syncs to the afternoon",
                ],
                "respecting": ["No sharing private family data"],
            },
        ],
        "alignment": {"claims": ["pref_focus"], "values": ["Self-Direction"]},
        "meta": {
            "llm_model": "fake-ollama",
            "prompt_path": "prompts/advise.md",
            "created_at": f"{day}T10:00:00Z",
        },
    }
    _write(advice_path, yaml.safe_dump(payload, sort_keys=False))
    return advice_path


def _seed_profile_suggestions(tmp_path: Path, day: str = DATE) -> Path:
    suggestions_path = tmp_path / "derived" / "profile_suggestions" / f"{day}.yaml"
    payload = {
        "day": day,
        "upserts": [
            {
                "target": "claims",
                "operation": "upsert",
                "value": make_claim_atom(
                    "pref_afternoon_break",
                    "Energy dips shortly after 15:00",
                    strength=0.68,
                    status="tentative",
                    last_updated=f"{day}T11:00:00Z",
                ),
            },
        ],
        "updates": [],
    }
    _write(suggestions_path, yaml.safe_dump(payload, sort_keys=False))
    return suggestions_path


def _seed_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config" / "config.yaml"
    _write(
        config_path,
        """
model: fake-ollama
seed: 7
impact_weights:
  values_goals: 1.2
        """,
    )
    return config_path


def _seed_prompt(tmp_path: Path, name: str = "history_context.md") -> Path:
    prompt_path = tmp_path / "prompts" / name
    _write(
        prompt_path,
        """
{{context}}
Summarize historical patterns.
        """,
    )
    return prompt_path


def _seed_journal_entry(
    tmp_path: Path,
    day: str,
    slug: str,
    body: str | None = None,
) -> Path:
    year, month, day_part = day.split("-")
    journal_path = tmp_path / "data" / "journal" / year / month / day_part / f"{slug}.md"
    text = (
        "---\n"
        f"id: {slug}\n"
        f"created_at: {day}T06:00:00Z\n"
        f"title: {slug.replace('-', ' ').title()}\n"
        "---\n\n" + (body or f"Daily reflections for {day}.")
    )
    _write(journal_path, text)
    return journal_path


def test_pack_l1_includes_profile_and_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)

    result = runner.invoke(app, ["pack", "--level", "L1", "--format", "yaml"])
    assert result.exit_code == 0, result.output
    assert "traits" in result.output
    assert "claims:" in result.output


def test_pack_l2_includes_daily_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    _seed_daily_artifacts(tmp_path)

    result = runner.invoke(app, ["pack", "--level", "L2", "--date", DATE])
    assert result.exit_code == 0
    assert "normalized" in result.output or ENTRY_ID in result.output
    assert "summaries" in result.output


def test_pack_missing_profile_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["pack", "--level", "L1"])
    assert result.exit_code != 0
    assert "self_profile" in result.output.lower()


def test_pack_trims_to_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    big_text = "sentence " * 500
    _write(tmp_path / "data" / "normalized" / DATE / "big.yaml", big_text)

    result = runner.invoke(
        app,
        ["pack", "--level", "L2", "--date", DATE, "--max-tokens", "50"],
    )
    assert result.exit_code == 0
    assert "trimmed" in result.output.lower()


def test_pack_output_file_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    out_path = tmp_path / "derived" / "packs" / "l1.yaml"

    first = runner.invoke(app, ["pack", "--level", "L1", "--output", str(out_path)])
    assert first.exit_code == 0
    mtime = out_path.stat().st_mtime

    second = runner.invoke(app, ["pack", "--level", "L1", "--output", str(out_path)])
    assert second.exit_code == 0
    assert out_path.stat().st_mtime == mtime


def test_pack_dry_run_lists_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    _seed_daily_artifacts(tmp_path)

    result = runner.invoke(app, ["pack", "--level", "L2", "--dry-run"])
    assert result.exit_code == 0
    assert "profile/self_profile.yaml" in result.output
    assert "normalized" in result.output


def test_pack_deterministic_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    _seed_daily_artifacts(tmp_path)

    first = runner.invoke(app, ["pack", "--level", "L2", "--date", DATE])
    assert first.exit_code == 0
    second = runner.invoke(app, ["pack", "--level", "L2", "--date", DATE])
    assert second.exit_code == 0
    assert first.output == second.output


def test_pack_json_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)

    result = runner.invoke(app, ["pack", "--level", "L1", "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["level"] == "L1"


def test_pack_l3_includes_advice_and_profile_suggestions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    _seed_daily_artifacts(tmp_path)
    advice_path = _seed_advice(tmp_path)
    suggestions_path = _seed_profile_suggestions(tmp_path)

    result = runner.invoke(app, ["pack", "--level", "L3", "--date", DATE])
    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    files = [entry["path"] for entry in payload.get("files", [])]
    assert str(advice_path.relative_to(tmp_path)) in files
    assert str(suggestions_path.relative_to(tmp_path)) in files


def test_pack_l4_history_days_includes_prior_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    _seed_daily_artifacts(tmp_path)
    prior_entry = _seed_daily_artifacts(tmp_path, day=PRIOR_DATE, entry_id=PRIOR_ENTRY_ID)
    raw_path = _seed_journal_entry(tmp_path, PRIOR_DATE, PRIOR_ENTRY_ID)
    config_path = _seed_config(tmp_path)
    prompt_path = _seed_prompt(tmp_path)

    result = runner.invoke(
        app,
        ["pack", "--level", "L4", "--date", DATE, "--history-days", "1"],
    )
    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    paths = {entry["path"] for entry in payload.get("files", [])}
    assert f"data/normalized/{PRIOR_DATE}/{prior_entry}.yaml" in paths
    assert f"derived/summaries/{PRIOR_DATE}.yaml" in paths
    assert f"derived/microfacts/{PRIOR_DATE}.yaml" in paths
    assert str(raw_path.relative_to(tmp_path)) in paths
    assert str(config_path.relative_to(tmp_path)) in paths
    assert str(prompt_path.relative_to(tmp_path)) in paths


def test_pack_l4_trimming_prioritizes_raw_journal_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    _seed_daily_artifacts(tmp_path)
    _seed_config(tmp_path)
    _seed_prompt(tmp_path)
    raw_path = _seed_journal_entry(
        tmp_path,
        DATE,
        "overlong-notes",
        body=" ".join(["raw"] * 800),
    )

    result = runner.invoke(
        app,
        [
            "pack",
            "--level",
            "L4",
            "--date",
            DATE,
            "--history-days",
            "0",
            "--max-tokens",
            "40",
        ],
    )
    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    trimmed = payload.get("meta", {}).get("trimmed", [])
    assert trimmed, "expected trimming metadata"
    first_trimmed = trimmed[0]
    assert first_trimmed["role"] == "journal_raw"
    assert first_trimmed["path"] == str(raw_path.relative_to(tmp_path))
    profile_entry = next(entry for entry in payload["files"] if entry["role"] == "profile")
    assert profile_entry["tokens"] > 0


def test_pack_l4_handles_missing_optional_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    _seed_daily_artifacts(tmp_path)

    result = runner.invoke(
        app,
        ["pack", "--level", "L4", "--date", DATE, "--history-days", "2"],
    )
    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    paths = [entry["path"] for entry in payload.get("files", [])]
    assert all("profile_suggestions" not in path for path in paths)


def test_pack_l4_supports_json_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    normalized_entry = _seed_daily_artifacts(tmp_path)
    _seed_profile(tmp_path)
    _seed_config(tmp_path)
    _seed_prompt(tmp_path, "history_context.md")

    result = runner.invoke(
        app,
        [
            "pack",
            "--level",
            "L4",
            "--date",
            DATE,
            "--history-days",
            "0",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["level"] == "L4"
    json_paths = [entry["path"] for entry in payload.get("files", [])]
    expected_normalized = f"data/normalized/{DATE}/{normalized_entry}.yaml"
    assert expected_normalized in json_paths


def test_pack_l4_dry_run_lists_expected_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_profile(tmp_path)
    _seed_daily_artifacts(tmp_path)
    _seed_daily_artifacts(tmp_path, day=PRIOR_DATE, entry_id=PRIOR_ENTRY_ID)
    _seed_advice(tmp_path)
    _seed_profile_suggestions(tmp_path)
    _seed_config(tmp_path)
    _seed_prompt(tmp_path)
    _seed_journal_entry(tmp_path, DATE, "focus-journal")

    result = runner.invoke(
        app,
        [
            "pack",
            "--level",
            "L4",
            "--date",
            DATE,
            "--history-days",
            "1",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "Planned files:" in result.output
    assert "profile/self_profile.yaml" in result.output
    assert "derived/advice" in result.output
    assert "derived/profile_suggestions" in result.output
