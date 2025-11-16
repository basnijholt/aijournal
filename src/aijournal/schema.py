"""Pydantic-backed validation helpers for aijournal payloads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from aijournal.domain.facts import DailySummary, MicroFactsFile
from aijournal.domain.journal import NormalizedEntry
from aijournal.domain.persona import InterviewSet, PersonaCore
from aijournal.models.authoritative import ClaimsFile, JournalEntry, SelfProfile
from aijournal.models.derived import (
    AdviceCard,
    ProfileUpdateBatch,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


class SchemaValidationError(ValueError):
    """Raised when a payload does not conform to a named schema."""

    def __init__(self, schema: str, errors: Iterable[str]) -> None:
        self.schema = schema
        self.errors = list(errors)
        message = f"Schema '{schema}' validation failed: {'; '.join(self.errors)}"
        super().__init__(message)


_MODEL_REGISTRY: dict[str, type[BaseModel]] = {
    "advice": AdviceCard,
    "claims": ClaimsFile,
    "interviews": InterviewSet,
    "journal_entry": JournalEntry,
    "microfacts": MicroFactsFile,
    "normalized_entry": NormalizedEntry,
    "persona_core": PersonaCore,
    "profile_updates": ProfileUpdateBatch,
    "self_profile": SelfProfile,
    "summary": DailySummary,
}


def _resolve_model(schema_name: str) -> type[BaseModel]:
    try:
        return _MODEL_REGISTRY[schema_name]
    except KeyError as exc:  # pragma: no cover - defensive guard
        msg = f"Unknown schema requested: {schema_name}"
        raise ValueError(msg) from exc


def validate_schema(schema_name: str, payload: Any) -> None:
    """Validate payload against the named schema or raise SchemaValidationError."""
    model = _resolve_model(schema_name)
    errors: list[str] = []
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        for err in exc.errors():
            location = ".".join(str(part) for part in err.get("loc", ())) or "<root>"
            errors.append(f"{location}: {err.get('msg', 'invalid value')}")
    if errors:
        raise SchemaValidationError(schema_name, errors)
