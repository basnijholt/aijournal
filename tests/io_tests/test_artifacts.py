from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from aijournal.common.base import StrictModel
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.io.artifacts import load_artifact, load_artifact_data, save_artifact

if TYPE_CHECKING:
    from pathlib import Path


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
    assert "schema" not in loaded_yaml
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


def test_load_artifact_data_returns_payload(tmp_path: Path) -> None:
    artifact = _make_artifact(5)
    path = tmp_path / "example.yaml"
    save_artifact(path, artifact)

    payload = load_artifact_data(path, _Payload)
    assert isinstance(payload, _Payload)
    assert payload.value == 5
