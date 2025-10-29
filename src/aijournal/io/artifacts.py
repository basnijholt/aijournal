"""Deterministic artifact serialization helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar, cast

import yaml
from pydantic import BaseModel, TypeAdapter

from aijournal.common.meta import Artifact, ArtifactKind

T = TypeVar("T", bound=BaseModel)


def _ensure_trailing_newline(payload: str) -> str:
    return payload if payload.endswith("\n") else payload + "\n"


def _dump_yaml(data: Any) -> str:
    serialized = yaml.safe_dump(
        data,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=False,
    )
    return _ensure_trailing_newline(serialized)


def _dump_json(data: Any) -> str:
    serialized = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False)
    return _ensure_trailing_newline(serialized)


def _load_raw(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml", ""}:
        return yaml.safe_load(text)
    if path.suffix == ".json":
        return json.loads(text)
    # Fall back to YAML for unknown extensions to ease migration.
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:  # pragma: no cover - defensive branch
        raise ValueError(f"Unsupported artifact extension: {path.suffix}") from exc


def save_artifact(path: Path | str, artifact: Artifact[T], *, format: str | None = None) -> None:
    """Persist an artifact envelope using deterministic ordering and UTF-8."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    data = artifact.model_dump(mode="json", by_alias=True, exclude_none=False)
    fmt = (format or target.suffix.lstrip(".") or "yaml").lower()

    if fmt in {"yaml", "yml"}:
        serialized = _dump_yaml(data)
    elif fmt == "json":
        serialized = _dump_json(data)
    else:
        raise ValueError(f"Unsupported artifact format: {fmt}")

    if target.exists() and target.read_text(encoding="utf-8") == serialized:
        return

    target.write_text(serialized, encoding="utf-8")


def load_artifact(path: Path | str, data_model: type[T]) -> Artifact[T]:
    """Load an artifact envelope from disk and validate the payload."""

    target = Path(path)
    raw = _load_raw(target)
    if isinstance(raw, dict) and isinstance(raw.get("kind"), str):
        raw = {**raw, "kind": ArtifactKind(raw["kind"])}

    artifact_any = Artifact[Any].model_validate(raw, strict=True)
    data = data_model.model_validate(artifact_any.data)
    artifact_typed = artifact_any.model_copy(update={"data": data})
    return cast(Artifact[T], artifact_typed)


def read_legacy_or_artifact(
    path: Path | str,
    legacy_model: type[T],
    *,
    artifact_model: type[T] | None = None,
) -> T:
    """Read legacy payloads while accepting new Artifact envelopes."""

    target = Path(path)
    raw = _load_raw(target)

    if isinstance(raw, dict) and raw.get("schema") == "v2":
        if isinstance(raw.get("kind"), str):
            raw = {**raw, "kind": ArtifactKind(raw["kind"])}
        artifact_any = Artifact[Any].model_validate(raw, strict=True)
        payload_model = artifact_model or legacy_model
        data = payload_model.model_validate(artifact_any.data)
        return data

    adapter = TypeAdapter(legacy_model)
    return adapter.validate_python(raw)


__all__ = ["save_artifact", "load_artifact", "read_legacy_or_artifact"]
