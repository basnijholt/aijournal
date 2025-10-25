"""JSON Schema validation helpers for derived artifacts."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, List

from jsonschema import Draft202012Validator


class SchemaValidationError(ValueError):
    """Raised when a payload does not conform to a named schema."""

    def __init__(self, schema: str, errors: Iterable[str]):
        self.schema = schema
        self.errors = list(errors)
        message = f"Schema '{schema}' validation failed: {'; '.join(self.errors)}"
        super().__init__(message)


def _schema_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "schemas"


@lru_cache(maxsize=None)
def _load_schema(schema_name: str) -> dict[str, Any]:
    path = _schema_dir() / f"{schema_name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Schema file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _get_validator(schema_name: str) -> Draft202012Validator:
    schema = _load_schema(schema_name)
    return Draft202012Validator(schema)


def validate_schema(schema_name: str, payload: Any) -> None:
    """Validate payload against the named schema or raise SchemaValidationError."""

    validator = _get_validator(schema_name)
    errors: List[str] = []
    for error in validator.iter_errors(payload):
        location = ".".join(str(part) for part in error.path) or "<root>"
        errors.append(f"{location}: {error.message}")
    if errors:
        raise SchemaValidationError(schema_name, errors)


__all__ = ["SchemaValidationError", "validate_schema"]
