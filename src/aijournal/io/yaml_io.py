"""Typed YAML serialization helpers for Pydantic models."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, TypeVar

import yaml
from pydantic import BaseModel

if TYPE_CHECKING:
    from pathlib import Path

T = TypeVar("T", bound=BaseModel)


class _EnumSafeDumper(yaml.SafeDumper):
    """YAML dumper that serializes enum instances as their values."""


def _enum_representer(dumper: _EnumSafeDumper, value: Enum) -> yaml.Node:
    payload = value.value if hasattr(value, "value") else value
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(payload))


_EnumSafeDumper.add_multi_representer(Enum, _enum_representer)


def _str_representer(dumper: _EnumSafeDumper, value: str) -> yaml.Node:
    """Render unicode directly and pretty-print multiline scalars."""
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_EnumSafeDumper.add_representer(str, _str_representer)


def _read_yaml(path: Path) -> Any:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if data is not None else {}


def load_yaml_model(path: Path, cls: type[T], *, default: T | None = None) -> T:
    """Load a YAML document into the requested Pydantic model."""
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    data = _read_yaml(path)
    return cls.model_validate(data)


def dump_yaml(data: Any, *, sort_keys: bool = False) -> str:
    """Serialize arbitrary data to YAML using the enum-safe dumper."""
    return yaml.dump(
        data,
        Dumper=_EnumSafeDumper,
        sort_keys=sort_keys,
        allow_unicode=True,
    )


def write_yaml_model(path: Path, instance: T) -> None:
    """Persist a Pydantic model instance to YAML on disk."""
    payload = instance.model_dump(mode="python", exclude_none=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = dump_yaml(payload, sort_keys=False)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == serialized:
            return
    path.write_text(serialized, encoding="utf-8")
