"""Domain-level models shared across the project."""

from .evidence import SourceRef, Span, redact_source_text
from .journal import NormalizedEntity, NormalizedEntry, Section

__all__ = [
    "Section",
    "NormalizedEntity",
    "NormalizedEntry",
    "Span",
    "SourceRef",
    "redact_source_text",
]
