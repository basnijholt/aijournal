"""Tests for `aijournal export pack` CLI."""

from __future__ import annotations

import json
from math import ceil
from typing import TYPE_CHECKING

import yaml
from typer.testing import CliRunner

from aijournal.cli import app
from tests.helpers import make_claim_atom

if TYPE_CHECKING:
    from pathlib import Path

DATE = "2025-02-03"
ENTRY_ID = "2025-02-03-sync-notes"
PRIOR_DATE = "2025-02-02"
PRIOR_ENTRY_ID = f"{PRIOR_DATE}-retro-notes"
ADVICE_QUESTION = "How do I protect deep work blocks?"


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


def _ensure_persona_core(tmp_path: Path, cli_runner: CliRunner) -> Path:
    result = cli_runner.invoke(app, ["ops", "persona", "build"])
    assert result.exit_code == 0, result.output
    persona_path = tmp_path / "derived" / "persona" / "persona_core.yaml"
    assert persona_path.exists(), "persona_core.yaml should be created"
    return persona_path


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


def _seed_config(tmp_path: Path, *, char_per_token: float | None = None) -> Path:
    payload: dict[str, object] = {
        "model": "fake-ollama",
        "seed": 7,
        "impact_weights": {"values_goals": 1.2},
    }
    if char_per_token is not None:
        payload["token_estimator"] = {"char_per_token": char_per_token}
    config_path = tmp_path / "config" / "config.yaml"
    _write(config_path, yaml.safe_dump(payload, sort_keys=False))
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


def test_pack_l1_uses_persona_core(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    persona_path = _ensure_persona_core(cli_workspace, cli_runner)

    result = cli_runner.invoke(
        app,
        ["export", "pack", "--level", "L1", "--format", "yaml"],
    )
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.stdout)
    files = payload.get("files", [])
    assert len(files) == 1
    assert files[0]["path"] == str(persona_path.relative_to(cli_workspace))


def test_pack_l2_includes_daily_artifacts(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _ensure_persona_core(cli_workspace, cli_runner)
    entry_slug = _seed_daily_artifacts(cli_workspace)
    _seed_daily_artifacts(cli_workspace, day=PRIOR_DATE, entry_id=PRIOR_ENTRY_ID)

    result = cli_runner.invoke(
        app,
        ["export", "pack", "--level", "L2", "--date", DATE],
    )
    assert result.exit_code == 0
    payload = yaml.safe_load(result.stdout)
    paths = {entry["path"] for entry in payload.get("files", [])}
    assert "derived/persona/persona_core.yaml" in paths
    assert f"data/normalized/{DATE}/{entry_slug}.yaml" in paths
    assert f"derived/summaries/{DATE}.yaml" in paths
    assert f"derived/summaries/{PRIOR_DATE}.yaml" in paths
    assert f"derived/microfacts/{PRIOR_DATE}.yaml" in paths
    assert f"data/normalized/{PRIOR_DATE}/{PRIOR_ENTRY_ID}.yaml" not in paths


def test_pack_requires_persona_core(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)

    result = cli_runner.invoke(app, ["export", "pack", "--level", "L1"])
    assert result.exit_code != 0
    assert "persona core" in result.output.lower()


def test_pack_missing_profile_errors_for_l2(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _seed_daily_artifacts(cli_workspace)
    _ensure_persona_core(cli_workspace, cli_runner)
    (cli_workspace / "profile" / "self_profile.yaml").unlink()

    result = cli_runner.invoke(
        app,
        ["export", "pack", "--level", "L2", "--date", DATE],
    )
    assert result.exit_code != 0
    assert "self_profile" in result.output.lower()


def test_pack_warns_when_persona_stale(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _ensure_persona_core(cli_workspace, cli_runner)
    profile_path = cli_workspace / "profile" / "self_profile.yaml"
    existing = profile_path.read_text(encoding="utf-8")
    profile_path.write_text(existing + "\n# updated\n", encoding="utf-8")

    result = cli_runner.invoke(app, ["export", "pack", "--level", "L1"])
    assert result.exit_code == 0
    assert "persona core is stale" in result.output.lower()


def test_pack_trims_to_budget(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _ensure_persona_core(cli_workspace, cli_runner)
    big_text = "sentence " * 500
    _write(cli_workspace / "data" / "normalized" / DATE / "big.yaml", big_text)

    result = cli_runner.invoke(
        app,
        [
            "export",
            "pack",
            "--level",
            "L2",
            "--date",
            DATE,
            "--max-tokens",
            "50",
        ],
    )
    assert result.exit_code == 0
    assert "trimmed" in result.output.lower()


def test_pack_output_file_idempotent(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _ensure_persona_core(cli_workspace, cli_runner)
    out_path = cli_workspace / "derived" / "packs" / "l1.yaml"

    first = cli_runner.invoke(
        app,
        ["export", "pack", "--level", "L1", "--output", str(out_path)],
    )
    assert first.exit_code == 0
    mtime = out_path.stat().st_mtime

    second = cli_runner.invoke(
        app,
        ["export", "pack", "--level", "L1", "--output", str(out_path)],
    )
    assert second.exit_code == 0
    assert out_path.stat().st_mtime == mtime


def test_pack_dry_run_lists_files(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _ensure_persona_core(cli_workspace, cli_runner)
    _seed_daily_artifacts(cli_workspace)

    result = cli_runner.invoke(app, ["export", "pack", "--level", "L2", "--dry-run"])
    assert result.exit_code == 0
    assert "derived/persona/persona_core.yaml" in result.output
    assert "profile/self_profile.yaml" in result.output
    assert "normalized" in result.output


def test_pack_deterministic_order(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _ensure_persona_core(cli_workspace, cli_runner)
    _seed_daily_artifacts(cli_workspace)

    first = cli_runner.invoke(
        app,
        ["export", "pack", "--level", "L2", "--date", DATE],
    )
    assert first.exit_code == 0
    second = cli_runner.invoke(
        app,
        ["export", "pack", "--level", "L2", "--date", DATE],
    )
    assert second.exit_code == 0
    payload_first = yaml.safe_load(first.stdout)
    payload_second = yaml.safe_load(second.stdout)
    assert payload_first["level"] == payload_second["level"]
    assert {entry["path"] for entry in payload_first.get("files", [])} == {
        entry["path"] for entry in payload_second.get("files", [])
    }


def test_pack_json_format(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _ensure_persona_core(cli_workspace, cli_runner)

    result = cli_runner.invoke(
        app,
        ["export", "pack", "--level", "L1", "--format", "json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["level"] == "L1"


def test_pack_l3_includes_advice_and_profile_suggestions(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _ensure_persona_core(cli_workspace, cli_runner)
    _seed_daily_artifacts(cli_workspace)
    advice_path = _seed_advice(cli_workspace)
    suggestions_path = _seed_profile_suggestions(cli_workspace)

    result = cli_runner.invoke(
        app,
        ["export", "pack", "--level", "L3", "--date", DATE],
    )
    assert result.exit_code == 0
    payload = yaml.safe_load(result.stdout)
    files = [entry["path"] for entry in payload.get("files", [])]
    assert str(advice_path.relative_to(cli_workspace)) in files
    assert str(suggestions_path.relative_to(cli_workspace)) in files


def test_pack_l4_history_days_includes_prior_context(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _ensure_persona_core(cli_workspace, cli_runner)
    _seed_daily_artifacts(cli_workspace)
    prior_entry = _seed_daily_artifacts(cli_workspace, day=PRIOR_DATE, entry_id=PRIOR_ENTRY_ID)
    raw_path = _seed_journal_entry(cli_workspace, PRIOR_DATE, PRIOR_ENTRY_ID)
    config_path = _seed_config(cli_workspace)
    prompt_path = _seed_prompt(cli_workspace)

    result = cli_runner.invoke(
        app,
        [
            "export",
            "pack",
            "--level",
            "L4",
            "--date",
            DATE,
            "--history-days",
            "1",
        ],
    )
    assert result.exit_code == 0
    payload = yaml.safe_load(result.stdout)
    paths = {entry["path"] for entry in payload.get("files", [])}
    assert f"data/normalized/{PRIOR_DATE}/{prior_entry}.yaml" in paths
    assert f"derived/summaries/{PRIOR_DATE}.yaml" in paths
    assert f"derived/microfacts/{PRIOR_DATE}.yaml" in paths
    assert str(raw_path.relative_to(cli_workspace)) in paths
    assert str(config_path.relative_to(cli_workspace)) in paths
    assert str(prompt_path.relative_to(cli_workspace)) in paths


def test_pack_respects_token_estimator_config(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _seed_daily_artifacts(cli_workspace)
    _seed_config(cli_workspace, char_per_token=2.0)
    _ensure_persona_core(cli_workspace, cli_runner)

    result = cli_runner.invoke(
        app,
        ["export", "pack", "--level", "L2", "--date", DATE],
    )
    assert result.exit_code == 0
    payload = yaml.safe_load(result.stdout)
    files = payload.get("files", [])
    normalized_path = f"data/normalized/{DATE}/{ENTRY_ID}.yaml"

    normalized_entry = next(entry for entry in files if entry["path"] == normalized_path)
    normalized_file = (cli_workspace / "data" / "normalized" / DATE / f"{ENTRY_ID}.yaml").read_text(
        encoding="utf-8"
    )
    expected_tokens = ceil(len(normalized_file) / 2.0)
    assert normalized_entry["tokens"] == expected_tokens


def test_pack_l4_trimming_prioritizes_raw_journal_entries(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _ensure_persona_core(cli_workspace, cli_runner)
    _seed_daily_artifacts(cli_workspace)
    _seed_config(cli_workspace)
    _seed_prompt(cli_workspace)
    raw_path = _seed_journal_entry(
        cli_workspace,
        DATE,
        "overlong-notes",
        body=" ".join(["raw"] * 800),
    )

    result = cli_runner.invoke(
        app,
        [
            "export",
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
    payload = yaml.safe_load(result.stdout)
    trimmed = payload.get("meta", {}).get("trimmed", [])
    assert trimmed, "expected trimming metadata"
    first_trimmed = trimmed[0]
    assert first_trimmed["role"] == "journal_raw"
    assert first_trimmed["path"] == str(raw_path.relative_to(cli_workspace))
    assert all(item["role"] != "persona_core" for item in trimmed)


def test_pack_l4_handles_missing_optional_artifacts(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _ensure_persona_core(cli_workspace, cli_runner)
    _seed_daily_artifacts(cli_workspace)

    result = cli_runner.invoke(
        app,
        [
            "export",
            "pack",
            "--level",
            "L4",
            "--date",
            DATE,
            "--history-days",
            "2",
        ],
    )
    assert result.exit_code == 0
    payload = yaml.safe_load(result.stdout)
    paths = [entry["path"] for entry in payload.get("files", [])]
    assert all("profile_suggestions" not in path for path in paths)


def test_pack_l4_supports_json_output(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    normalized_entry = _seed_daily_artifacts(cli_workspace)
    _seed_profile(cli_workspace)
    _ensure_persona_core(cli_workspace, cli_runner)
    _seed_config(cli_workspace)
    _seed_prompt(cli_workspace, "history_context.md")

    result = cli_runner.invoke(
        app,
        [
            "export",
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
    payload = json.loads(result.stdout)
    assert payload["level"] == "L4"
    json_paths = [entry["path"] for entry in payload.get("files", [])]
    expected_normalized = f"data/normalized/{DATE}/{normalized_entry}.yaml"
    assert expected_normalized in json_paths


def test_pack_l4_dry_run_lists_expected_files(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _seed_profile(cli_workspace)
    _ensure_persona_core(cli_workspace, cli_runner)
    _seed_daily_artifacts(cli_workspace)
    _seed_daily_artifacts(cli_workspace, day=PRIOR_DATE, entry_id=PRIOR_ENTRY_ID)
    _seed_advice(cli_workspace)
    _seed_profile_suggestions(cli_workspace)
    _seed_config(cli_workspace)
    _seed_prompt(cli_workspace)
    _seed_journal_entry(cli_workspace, DATE, "focus-journal")

    result = cli_runner.invoke(
        app,
        [
            "export",
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
