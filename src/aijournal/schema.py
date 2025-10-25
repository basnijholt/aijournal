"""Pydantic-backed validation helpers for aijournal payloads."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Type

from pydantic import BaseModel, ValidationError

from aijournal.models import (
    AdviceCard,
    ClaimsFile,
    DailySummary,
    InterviewSet,
    JournalEntry,
    MicroFactsFile,
    NormalizedEntry,
    ProfileSuggestions,
    SelfProfile,
)


class SchemaValidationError(ValueError):
    """Raised when a payload does not conform to a named schema."""

    def __init__(self, schema: str, errors: Iterable[str]):
        self.schema = schema
        self.errors = list(errors)
        message = f"Schema '{schema}' validation failed: {'; '.join(self.errors)}"
        super().__init__(message)


_MODEL_REGISTRY: Dict[str, Type[BaseModel]] = {
    "advice": AdviceCard,
    "claims": ClaimsFile,
    "interviews": InterviewSet,
    "journal_entry": JournalEntry,
    "microfacts": MicroFactsFile,
    "normalized_entry": NormalizedEntry,
    "profile_suggestions": ProfileSuggestions,
    "self_profile": SelfProfile,
    "summary": DailySummary,
}


def _resolve_model(schema_name: str) -> Type[BaseModel]:
    try:
        return _MODEL_REGISTRY[schema_name]
    except KeyError as exc:  # pragma: no cover - defensive guard
        raise ValueError(f"Unknown schema requested: {schema_name}") from exc


def validate_schema(schema_name: str, payload: Any) -> None:
    """Validate payload against the named schema or raise SchemaValidationError."""

    model = _resolve_model(schema_name)
    errors: List[str] = []
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        for err in exc.errors():
            location = ".".join(str(part) for part in err.get("loc", ())) or "<root>"
            errors.append(f"{location}: {err.get('msg', 'invalid value')}")
    if errors:
        raise SchemaValidationError(schema_name, errors)


__all__ = ["SchemaValidationError", "validate_schema"]

