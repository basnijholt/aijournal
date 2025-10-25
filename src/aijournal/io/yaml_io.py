"""Typed YAML serialization helpers for dataclasses."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Type, TypeVar

import yaml
from cattrs import GenConverter

T = TypeVar("T")

_converter = GenConverter(forbid_extra_keys=False)


def _read_yaml(path: Path) -> Any:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if data is not None else {}


def load_yaml_model(path: Path, cls: Type[T], *, default: Optional[T] = None) -> T:
    """Load a YAML document into the requested dataclass."""

    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    data = _read_yaml(path)
    return _converter.structure(data, cls)


def write_yaml_model(path: Path, instance: T) -> None:
    """Persist a dataclass instance to YAML on disk."""

    payload = _converter.unstructure(instance)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
