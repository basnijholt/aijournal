"""Schemas and entry point for the capture orchestrator (Phase 2 scaffold)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CaptureInput(BaseModel):
    """User-provided options for a capture run."""

    source: Literal["stdin", "editor", "file", "dir"] = Field(
        ...,
        description="Primary source for captured content.",
    )
    text: str | None = Field(None, description="Raw text provided on the CLI.")
    paths: list[str] = Field(default_factory=list, description="Paths to capture from.")
    source_type: Literal["journal", "notes", "blog"] = Field(
        "journal",
        description="Semantic classification of the captured material.",
    )
    date: str | None = Field(None, description="Override created_at date (YYYY-MM-DD).")
    title: str | None = Field(None, description="Override title for captured entries.")
    slug: str | None = Field(None, description="Explicit slug to use when persisting.")
    tags: list[str] = Field(default_factory=list, description="Tags to merge into front matter.")
    projects: list[str] = Field(
        default_factory=list,
        description="Projects to merge into front matter.",
    )
    mood: str | None = Field(None, description="Mood value to record in front matter.")
    apply_profile: Literal["auto", "review"] = Field(
        "auto",
        description="How profile updates should be applied after derivations.",
    )
    rebuild: Literal["auto", "always", "skip"] = Field(
        "auto",
        description="How index/persona rebuilds should be triggered.",
    )
    pack: Literal["L1", "L3", "L4"] | None = Field(
        None,
        description="Optional pack level to emit when persona changes.",
    )
    retries: int = Field(1, ge=0, description="LLM structured-output retries per stage.")
    progress: bool = Field(True, description="Whether to display progress indicators.")
    dry_run: bool = Field(False, description="Skip writes and report planned actions only.")


class EntryResult(BaseModel):
    """Outcome for a single journal entry processed during capture."""

    markdown_path: str | None = Field(None, description="Authoritative Markdown path.")
    normalized_path: str | None = Field(None, description="Normalized YAML emitted for the entry.")
    date: str = Field(..., description="Date bucket for the entry (YYYY-MM-DD).")
    slug: str = Field(..., description="Slug assigned to the entry.")
    deduped: bool = Field(
        False, description="True when the input was skipped due to identical hash."
    )
    changed: bool = Field(False, description="True when content or metadata changed on disk.")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal issues encountered.")


class CaptureResult(BaseModel):
    """Aggregate result for a capture run."""

    run_id: str = Field(..., description="Unique identifier for the capture run.")
    entries: list[EntryResult] = Field(default_factory=list, description="Per-entry outcomes.")
    artifacts_changed: dict[str, int] = Field(
        default_factory=dict,
        description="Counts of downstream artifacts touched by type.",
    )
    persona_stale_before: bool = Field(
        False,
        description="Whether persona was stale before capture executed.",
    )
    persona_stale_after: bool = Field(
        False,
        description="Whether persona remains stale after capture steps.",
    )
    index_rebuilt: bool = Field(False, description="True when the index was fully rebuilt.")
    warnings: list[str] = Field(default_factory=list, description="Warnings raised during capture.")
    errors: list[str] = Field(default_factory=list, description="Fatal errors encountered.")
    durations_ms: dict[str, int] = Field(
        default_factory=dict,
        description="Per-stage durations (milliseconds).",
    )


def run_capture(_input: CaptureInput) -> CaptureResult:
    """Execute the capture workflow (stub for Phase 2)."""

    raise NotImplementedError("capture service not implemented yet")
