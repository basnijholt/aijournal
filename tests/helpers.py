"""Test utilities for building structured fixtures."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aijournal.io.yaml_io import dump_yaml


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
            "observation_count": 1,
        },
    }


def write_normalized_entry(
    base: Path,
    *,
    date: str,
    entry_id: str,
    summary: str,
    tags: list[str] | None = None,
    source_hash: str | None = None,
    source_type: str = "journal",
) -> Path:
    """Create a normalized entry YAML under data/normalized for tests."""
    tags_list = tags or ["focus"]
    path = base / "data" / "normalized" / date / f"{entry_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "id": entry_id,
        "created_at": f"{date}T09:00:00Z",
        "source_path": f"data/journal/{date.replace('-', '/')}/{entry_id}.md",
        "title": summary.split()[0].capitalize(),
        "tags": tags_list,
        "summary": summary,
        "sections": [
            {"heading": "Context", "summary": summary},
        ],
        "source_hash": source_hash or f"hash-{entry_id}",
        "source_type": source_type,
    }
    path.write_text(dump_yaml(payload, sort_keys=False), encoding="utf-8")
    return path


def write_daily_summary(
    base: Path,
    *,
    date: str,
    bullets: list[str] | None = None,
    highlights: list[str] | None = None,
    todo_candidates: list[str] | None = None,
) -> Path:
    """Write a minimal Artifact[DailySummary] for tests."""
    payload = {
        "kind": "summaries.daily",
        "meta": {
            "created_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model": "fake-ollama",
            "prompt_path": "prompts/summarize_day.md",
            "prompt_hash": "test",
        },
        "data": {
            "day": date,
            "bullets": bullets or ["Captured daily snapshot"],
            "highlights": highlights or [],
            "todo_candidates": todo_candidates or [],
        },
    }
    path = base / "derived" / "summaries" / f"{date}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(payload, sort_keys=False), encoding="utf-8")
    return path


def write_manifest(base: Path, entries: list[dict[str, Any]]) -> Path:
    path = base / "data" / "manifest" / "ingested.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(entries, sort_keys=False), encoding="utf-8")
    return path


def read_index_meta(base: Path) -> dict[str, Any]:
    meta_path = base / "derived" / "index" / "meta.json"
    if not meta_path.exists():
        return {}
    return json.loads(meta_path.read_text(encoding="utf-8"))


def copy_fixture_workspace(name: str, destination: Path) -> Path:
    """Copy a named fixture workspace into the destination directory."""
    fixture_root = FIXTURES_ROOT / name
    if not fixture_root.exists():
        msg = f"Fixture {name} not found under {FIXTURES_ROOT}"
        raise FileNotFoundError(msg)
    for item in fixture_root.iterdir():
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
    return destination


FIXTURES_ROOT = Path(__file__).parent / "fixtures"
