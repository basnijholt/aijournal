"""Lightweight helpers for coercing loosely typed config values."""

from __future__ import annotations

from typing import Any


def coerce_float(value: Any) -> float | None:
    """Best-effort float conversion; returns None when coercion fails."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def coerce_int(value: Any) -> int | None:
    """Best-effort int conversion; returns None when coercion fails."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
