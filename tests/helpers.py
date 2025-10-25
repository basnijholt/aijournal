"""Test utilities for building structured fixtures."""

from __future__ import annotations

from datetime import UTC, datetime


def make_claim_atom(
    claim_id: str,
    statement: str,
    *,
    subject: str | None = None,
    predicate: str = "insight",
    value: str | None = None,
    strength: float = 0.7,
    status: str = "accepted",
    method: str = "inferred",
    first_seen: str = "2025-01-01",
    last_updated: str | None = None,
) -> dict:
    """Return a claim atom dict that matches the new schema."""

    scope_context = []
    timestamp = last_updated or datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": claim_id,
        "type": "preference",
        "subject": subject or claim_id,
        "predicate": predicate,
        "value": value or statement,
        "statement": statement,
        "scope": {
            "domain": None,
            "context": scope_context,
            "conditions": [],
        },
        "strength": strength,
        "status": status,
        "method": method,
        "user_verified": False,
        "review_after_days": 120,
        "provenance": {
            "sources": [
                {
                    "entry_id": "seed-entry",
                    "spans": [],
                },
            ],
            "first_seen": first_seen,
            "last_updated": timestamp,
        },
    }
