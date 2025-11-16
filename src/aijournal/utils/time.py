"""Time and formatting helpers shared across aijournal modules."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(tz=UTC)


def format_timestamp(dt: datetime) -> str:
    """Format a datetime into ISO-8601 (UTC) without offset suffix."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify_title(title: str) -> str:
    """Produce a filesystem-friendly slug from free-form text."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "entry"


def generate_session_id(clock: Callable[[], datetime] = now) -> str:
    """Generate a session identifier using the provided clock."""
    return f"chat-{clock().strftime('%Y%m%d-%H%M%S')}"


def created_date(created_at: str) -> str:
    """Strip the time component from an ISO-like timestamp string."""
    if "T" in created_at:
        return created_at.split("T", 1)[0]
    return created_at
