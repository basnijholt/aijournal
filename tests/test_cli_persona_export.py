"""CLI tests for `aijournal persona export`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import typer

from aijournal.cli import (
    _normalize_persona_variants,
    _validate_persona_export_flags,
    app,
)
from aijournal.io.yaml_io import dump_yaml
from aijournal.services.persona_export import PersonaVariant
from tests.helpers import make_claim_atom

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import CliRunner


def _seed_profile(workspace: Path) -> None:
    profile_dir = workspace / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "traits": {
            "identity": {
                "summary": "Local-first engineer focusing on reproducible systems.",
                "highlights": ["Ships deterministic tooling", "Prefers async collaboration"],
            },
        },
        "values_motivations": {
            "core_values": [
                {"value": "Craftsmanship", "why": "Quality over shortcuts."},
            ],
        },
        "boundaries_ethics": {
            "guardrails": ["Never leak personal family data."],
        },
        "coaching_prefs": {
            "tone": "direct",
            "prompts": ["Flag trade-offs", "Ask for clarifications"],
        },
    }
    (profile_dir / "self_profile.yaml").write_text(
        dump_yaml(payload, sort_keys=False),
        encoding="utf-8",
    )


def _seed_claims(workspace: Path) -> None:
    claims_dir = workspace / "profile"
    claims_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "claims": [
            make_claim_atom(
                "pref.morning",
                "Mornings are best for deep work",
                subject="focus",
                predicate="window",
                strength=0.82,
                last_updated="2025-02-01T08:00:00Z",
            ),
            make_claim_atom(
                "pref.async",
                "Async reviews preferred",
                subject="collab",
                predicate="style",
                strength=0.61,
                last_updated="2025-02-02T08:00:00Z",
            ),
        ],
    }
    (claims_dir / "claims.yaml").write_text(
        dump_yaml(payload, sort_keys=False),
        encoding="utf-8",
    )


def _ensure_persona_core(workspace: Path, cli_runner: CliRunner) -> Path:
    _seed_profile(workspace)
    _seed_claims(workspace)
    result = cli_runner.invoke(app, ["ops", "persona", "build"])
    assert result.exit_code == 0, result.stdout
    persona_path = workspace / "derived" / "persona" / "persona_core.yaml"
    assert persona_path.exists()
    return persona_path


def test_persona_export_defaults_to_stdout(cli_workspace: Path, cli_runner: CliRunner) -> None:
    _ensure_persona_core(cli_workspace, cli_runner)

    result = cli_runner.invoke(app, ["ops", "persona", "export", "--variant", "tiny"])
    assert result.exit_code == 0, result.stdout
    assert "# Persona Context" in result.stdout
    assert "Instructions for the assistant" in result.stdout


def test_persona_export_writes_file_and_respects_overwrite(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _ensure_persona_core(cli_workspace, cli_runner)
    output_path = cli_workspace / "derived" / "persona.md"

    first = cli_runner.invoke(
        app,
        [
            "ops",
            "persona",
            "export",
            "--output",
            str(output_path.relative_to(cli_workspace)),
        ],
    )
    assert first.exit_code == 0, first.stdout
    assert output_path.exists()

    second = cli_runner.invoke(
        app,
        [
            "ops",
            "persona",
            "export",
            "--output",
            str(output_path.relative_to(cli_workspace)),
        ],
    )
    assert second.exit_code != 0
    assert "Refusing to overwrite" in second.stderr or second.stdout

    third = cli_runner.invoke(
        app,
        [
            "ops",
            "persona",
            "export",
            "--output",
            str(output_path.relative_to(cli_workspace)),
            "--overwrite",
        ],
    )
    assert third.exit_code == 0, third.stdout


def test_persona_export_validates_token_override(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _ensure_persona_core(cli_workspace, cli_runner)

    result = cli_runner.invoke(app, ["ops", "persona", "export", "--tokens", "0"])
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "")
    assert "Usage:" in combined  # Typer emits usage with the validation failure


def test_persona_export_errors_when_persona_missing(tmp_path: Path, cli_runner: CliRunner) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    cli_runner.invoke(app, ["--path", str(tmp_path), "init"])
    result = cli_runner.invoke(
        app,
        ["--path", str(tmp_path), "ops", "persona", "export"],
        env={"AIJOURNAL_FAKE_OLLAMA": "1"},
    )
    assert result.exit_code == 1
    combined = (result.stdout or "") + (result.stderr or "")
    assert "persona" in combined.lower()


def test_persona_export_multiple_variants_stdout(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _ensure_persona_core(cli_workspace, cli_runner)

    result = cli_runner.invoke(
        app,
        ["ops", "persona", "export", "--variant", "tiny", "--variant", "short"],
    )
    assert result.exit_code == 0, result.stdout
    output = result.stdout
    assert output.count("# Persona Context") == 2
    assert "<!-- persona:tiny" in output
    assert "<!-- persona:short" in output
    assert "\n---\n" in output


def test_persona_export_output_dir_writes_all_variants(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _ensure_persona_core(cli_workspace, cli_runner)
    destination = cli_workspace / "derived" / "cards"

    result = cli_runner.invoke(
        app,
        [
            "ops",
            "persona",
            "export",
            "--variant",
            "all",
            "--output-dir",
            str(destination.relative_to(cli_workspace)),
            "--overwrite",
        ],
    )
    assert result.exit_code == 0, result.stdout

    files = sorted(destination.glob("*.md"))
    names = [path.name for path in files]
    assert any("tiny" in name for name in names)
    assert any("short" in name for name in names)
    assert any("full" in name for name in names)


def test_persona_export_disallows_token_override_with_multiple_variants(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _ensure_persona_core(cli_workspace, cli_runner)

    result = cli_runner.invoke(
        app,
        [
            "ops",
            "persona",
            "export",
            "--variant",
            "tiny",
            "--variant",
            "short",
            "--tokens",
            "400",
        ],
    )
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "")
    assert "only" in combined.lower()


def test_persona_export_disallows_output_with_multiple_variants(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    _ensure_persona_core(cli_workspace, cli_runner)
    destination = cli_workspace / "derived" / "persona-multi.md"

    result = cli_runner.invoke(
        app,
        [
            "ops",
            "persona",
            "export",
            "--variant",
            "tiny",
            "--variant",
            "short",
            "--output",
            str(destination.relative_to(cli_workspace)),
        ],
    )
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "")
    assert "output" in combined.lower()


def test_normalize_persona_variants_handles_all_keyword() -> None:
    result = _normalize_persona_variants(["all"])
    assert result == list(PersonaVariant)


def test_normalize_persona_variants_defaults_to_short() -> None:
    result = _normalize_persona_variants([])
    assert result == [PersonaVariant.SHORT]


def test_validate_persona_export_flags_detects_conflicts(tmp_path: Path) -> None:
    destination = tmp_path / "persona.md"

    with pytest.raises(typer.BadParameter):
        _validate_persona_export_flags(
            expanded_variants=[PersonaVariant.TINY, PersonaVariant.SHORT],
            tokens=100,
            max_items=None,
            output=None,
            output_dir=None,
        )

    with pytest.raises(typer.BadParameter):
        _validate_persona_export_flags(
            expanded_variants=[PersonaVariant.TINY, PersonaVariant.SHORT],
            tokens=None,
            max_items=None,
            output=destination,
            output_dir=None,
        )
