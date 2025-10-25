"""Typed YAML serialization helpers for Pydantic models."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Type, TypeVar

import yaml
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def _read_yaml(path: Path) -> Any:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if data is not None else {}


def load_yaml_model(path: Path, cls: Type[T], *, default: Optional[T] = None) -> T:
    """Load a YAML document into the requested Pydantic model."""

    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    data = _read_yaml(path)
    return cls.model_validate(data)


def write_yaml_model(path: Path, instance: T) -> None:
    """Persist a Pydantic model instance to YAML on disk."""

    payload = instance.model_dump(mode="python", exclude_none=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
