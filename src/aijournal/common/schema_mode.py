"""Helpers for configuring legacy vs. v2 schema behavior."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

SchemaMode = Literal[
    "read-legacy-write-new",
    "read-both-write-both",
    "read-new-write-new",
]

_DEFAULT_MODE: SchemaMode = "read-legacy-write-new"
_VALID_MODES: set[str] = {
    "read-legacy-write-new",
    "read-both-write-both",
    "read-new-write-new",
}


@lru_cache(maxsize=1)
def get_schema_mode() -> SchemaMode:
    """Return the active schema migration mode."""

    raw = os.getenv("AIJOURNAL_SCHEMA_MODE", _DEFAULT_MODE)
    value = raw.strip().lower()
    if value not in _VALID_MODES:
        return _DEFAULT_MODE
    return value  # type: ignore[return-value]


def reset_schema_mode_cache() -> None:
    """Reset the cached schema mode (useful for tests)."""

    get_schema_mode.cache_clear()


def allow_legacy_read() -> bool:
    """Return True when legacy payloads may be consumed."""

    return get_schema_mode() in {"read-legacy-write-new", "read-both-write-both"}


def should_dual_write() -> bool:
    """Return True when both legacy and v2 outputs should be produced."""

    return get_schema_mode() == "read-both-write-both"


__all__ = [
    "SchemaMode",
    "allow_legacy_read",
    "get_schema_mode",
    "reset_schema_mode_cache",
    "should_dual_write",
]
