"""Tests for the `aijournal ingest` command (fake LLM mode)."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Dict, List

import pytest
import yaml
from typer.testing import CliRunner

from aijournal.cli import app


runner = CliRunner()


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


def _read_yaml(path: Path) -> Dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_ingest_creates_normalized_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])  # ensure config/profile scaffolding

    post = _write_blog_post(tmp_path)
    env = {"AIJOURNAL_FAKE_OLLAMA": "1"}
    result = runner.invoke(app, ["ingest", str(post)], env=env)

    assert result.exit_code == 0, result.stdout
    normalized = tmp_path / "data" / "normalized" / "2025-08-25" / "2025-08-25-agentic-coding.yaml"
    assert normalized.exists()
    normalized_data = _read_yaml(normalized)
    assert normalized_data["title"] == "Agentic Coding"
    assert normalized_data["tags"] == ["ai", "productivity", "engineering"]
    assert normalized_data["source_type"] == "external"

    digest = sha256(post.read_bytes()).hexdigest()
    snapshot = tmp_path / "data" / "raw" / f"{digest}.md"
    assert snapshot.exists()

    manifest_path = tmp_path / "data" / "manifest" / "ingested.yaml"
    manifest = _read_yaml(manifest_path)
    assert isinstance(manifest, list)
    assert manifest[0]["hash"] == digest
    assert manifest[0]["normalized"].endswith("2025-08-25-agentic-coding.yaml")


def test_ingest_skips_duplicate_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])

    post = _write_blog_post(tmp_path)
    env = {"AIJOURNAL_FAKE_OLLAMA": "1"}
    first = runner.invoke(app, ["ingest", str(post)], env=env)
    assert first.exit_code == 0

    second = runner.invoke(app, ["ingest", str(post)], env=env)
    assert second.exit_code == 0
    assert "already ingested" in second.stdout

    manifest_path = tmp_path / "data" / "manifest" / "ingested.yaml"
    manifest: List[dict[str, object]] = _read_yaml(manifest_path)
    assert len(manifest) == 1
