"""Tests for the `aijournal ingest` command (fake LLM mode)."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

import yaml

from aijournal.cli import app
from aijournal.commands.ingest import _fake_structured_entry

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from typer.testing import CliRunner


def _write_blog_post(tmp_path: Path, slug: str = "agentic-coding") -> Path:
    post = tmp_path / "sources" / f"{slug}.md"
    post.parent.mkdir(parents=True, exist_ok=True)
    post.write_text(
        """---
id: agentic-coding
title: Agentic Coding
date: 2025-08-25T09:00:00Z
tags: [AI, Productivity]
categories: [Engineering]
summary: "Agentic tooling changed my workflows."
---

# Phase 1
Notes about the first phase.

## Phase 2
More context.
""",
        encoding="utf-8",
    )
    return post


def _read_yaml(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_ingest_creates_normalized_and_manifest(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    post = _write_blog_post(cli_workspace)
    result = cli_runner.invoke(app, ["ops", "pipeline", "ingest", str(post)])

    assert result.exit_code == 0, result.stdout
    normalized = (
        cli_workspace / "data" / "normalized" / "2025-08-25" / "2025-08-25-agentic-coding.yaml"
    )
    assert normalized.exists()
    normalized_data = _read_yaml(normalized)
    assert normalized_data["title"] == "Agentic Coding"
    assert {"ai", "productivity", "engineering"}.issubset(set(normalized_data["tags"]))
    assert normalized_data["source_type"] == "external"

    digest = sha256(post.read_bytes()).hexdigest()
    snapshot = cli_workspace / "data" / "raw" / f"{digest}.md"
    assert snapshot.exists()

    manifest_path = cli_workspace / "data" / "manifest" / "ingested.yaml"
    manifest = _read_yaml(manifest_path)
    assert isinstance(manifest, list)
    assert manifest[0]["hash"] == digest
    assert manifest[0]["normalized"].endswith("2025-08-25-agentic-coding.yaml")


def test_ingest_skips_duplicate_hash(
    cli_workspace: Path,
    cli_runner: CliRunner,
) -> None:
    post = _write_blog_post(cli_workspace)
    first = cli_runner.invoke(app, ["ops", "pipeline", "ingest", str(post)])
    assert first.exit_code == 0

    second = cli_runner.invoke(app, ["ops", "pipeline", "ingest", str(post)])
    assert second.exit_code == 0
    assert "already ingested" in second.stdout

    manifest_path = cli_workspace / "data" / "manifest" / "ingested.yaml"
    manifest: list[dict[str, object]] = _read_yaml(manifest_path)
    assert len(manifest) == 1


def test_fake_structured_entry_handles_malformed_frontmatter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = tmp_path / "broken.md"
    bad.write_text(
        '---\ntitle: "Broken\nsummary: Missing closing quote\n---\nBody text',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "aijournal.utils.time.now",
        lambda: datetime(2025, 1, 1, 8, 0, tzinfo=UTC),
    )

    result = _fake_structured_entry(bad)

    assert result.title == "broken"
    assert result.created_at.startswith("2025-01-01")
    assert result.sections
