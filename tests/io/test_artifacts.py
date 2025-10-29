from __future__ import annotations

from pathlib import Path

import yaml

from aijournal.common import Artifact, ArtifactKind, ArtifactMeta, StrictModel
from aijournal.io import load_artifact, read_legacy_or_artifact, save_artifact


class _Payload(StrictModel):
    value: int


def _make_artifact(value: int = 1) -> Artifact[_Payload]:
    return Artifact[_Payload](
        kind=ArtifactKind.SUMMARY_DAILY,
        meta=ArtifactMeta(created_at="2025-10-29T00:00:00Z"),
        data=_Payload(value=value),
    )


def test_save_artifact_writes_deterministic_yaml(tmp_path: Path) -> None:
    artifact = _make_artifact()
    path = tmp_path / "artifact.yaml"

    save_artifact(path, artifact)

    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert text.splitlines()[0] == "data:"

    loaded_yaml = yaml.safe_load(text)
    assert loaded_yaml["schema"] == "v2"
    assert loaded_yaml["kind"] == ArtifactKind.SUMMARY_DAILY.value


def test_save_artifact_json(tmp_path: Path) -> None:
    artifact = _make_artifact(2)
    path = tmp_path / "artifact.json"

    save_artifact(path, artifact)

    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert text.strip().startswith("{")

    loaded = load_artifact(path, _Payload)
    assert loaded.data.value == 2


def test_load_artifact_roundtrip(tmp_path: Path) -> None:
    artifact = _make_artifact(3)
    path = tmp_path / "artifact.yaml"
    save_artifact(path, artifact)

    loaded = load_artifact(path, _Payload)
    assert isinstance(loaded.data, _Payload)
    assert loaded.data.value == 3


def test_read_legacy_or_artifact_handles_both(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy.yaml"
    legacy_path.write_text("value: 7\n", encoding="utf-8")

    legacy_value = read_legacy_or_artifact(legacy_path, _Payload)
    assert isinstance(legacy_value, _Payload)
    assert legacy_value.value == 7

    artifact = _make_artifact(5)
    artifact_path = tmp_path / "wrapped.yaml"
    save_artifact(artifact_path, artifact)

    wrapped_value = read_legacy_or_artifact(artifact_path, _Payload)
    assert isinstance(wrapped_value, _Payload)
    assert wrapped_value.value == 5
