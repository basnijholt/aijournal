from __future__ import annotations

from typing import TYPE_CHECKING

from aijournal.common.app_config import AppConfig
from aijournal.services.capture import CaptureInput
from aijournal.services.capture.stages import stage7_persona

if TYPE_CHECKING:
    from pathlib import Path


def _make_inputs() -> CaptureInput:
    return CaptureInput(source="stdin", text="Persona test")


def test_stage7_persona_triggers_build(tmp_path: Path, monkeypatch) -> None:
    persona_dir = tmp_path / "derived" / "persona"
    persona_dir.mkdir(parents=True, exist_ok=True)

    states = [("stale", []), ("fresh", [])]

    monkeypatch.setattr(
        "aijournal.commands.persona.persona_state",
        lambda root, workspace, config: states.pop(0),
    )

    monkeypatch.setattr(
        "aijournal.commands.profile.load_profile_components",
        lambda *_, **__: (object(), []),
    )

    monkeypatch.setattr(
        "aijournal.commands.profile.profile_to_dict",
        lambda profile: {"name": "profile"},
    )

    monkeypatch.setattr(
        "aijournal.common.config_loader.load_config",
        lambda root: {},
    )

    persona_path = persona_dir / "persona_core.yaml"

    def fake_build(profile, claim_models, *, config, root):
        persona_path.write_text("persona", encoding="utf-8")
        return persona_path, True

    monkeypatch.setattr("aijournal.commands.persona.run_persona_build", fake_build)

    config = AppConfig()
    outputs = stage7_persona.run_persona_stage_7(
        _make_inputs(),
        tmp_path,
        config,
        {"profile": 1},
    )

    assert outputs.result.changed is True
    assert outputs.persona_changed is True
    assert outputs.persona_stale_before is True
    assert outputs.persona_stale_after is False


def test_stage7_persona_noop_when_fresh(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "aijournal.commands.persona.persona_state",
        lambda root, workspace, config: ("fresh", []),
    )
    monkeypatch.setattr(
        "aijournal.commands.profile.load_profile_components",
        lambda *_, **__: (None, []),
    )

    config = AppConfig()
    outputs = stage7_persona.run_persona_stage_7(
        _make_inputs(),
        tmp_path,
        config,
        {},
    )

    assert outputs.result.changed is False
    assert outputs.result.ok is True
    assert outputs.persona_changed is False
    assert outputs.persona_stale_before is False
