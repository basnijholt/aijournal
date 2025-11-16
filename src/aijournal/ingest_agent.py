"""Structured ingestion helpers powered by Pydantic AI."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from aijournal.common.types import TimestampStr  # noqa: TC001
from aijournal.domain.journal import Section as IngestSection  # noqa: TC001
from aijournal.services.ollama import build_ollama_agent, build_ollama_config_from_mapping
from aijournal.utils import time as time_utils

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic_ai import Agent

    from aijournal.common.app_config import AppConfig


INGEST_SYSTEM_PROMPT = """
You are part of a local journaling pipeline. Given a Markdown or Hugo document with optional
YAML/TOML front matter, extract the core metadata needed to normalize it into a journal entry.

Requirements:
- Always emit JSON that matches the provided schema. Do not include prose outside JSON.
- Prefer metadata from the front matter (title, dates, tags, categories). Fallback to the body
  when metadata is missing.
- `entry_id`: short slug (kebab-case) derived from `id`, `slug`, or the title. Do not include
  spaces. If a date is available, prefix the slug with YYYY-MM-DD.
- `created_at`: ISO 8601 timestamp with timezone. If the source only has a date, assume
  09:00:00Z on that date.
- `tags`: combine unique values from tags, categories, keywords, topics, or other obvious
  label lists. Prefer simple lowercase words (no sentences).
- `sections`: capture up to six major headings from the body. Each section summary is ≤25 words.
- `summary`: two sentences summarizing the entry in plain English.
- Ignore template directives (e.g., `{{< ... >}}`) and media links when extracting content.
- When no headings exist, synthesize a single section using the main idea of the entry.

Return concise, deterministic data so downstream commands can diff results easily.
"""


class IngestResult(BaseModel):
    """Structured output returned by the ingestion agent."""

    entry_id: str | None = Field(default=None, description="Slug or identifier for this entry")
    created_at: TimestampStr
    title: str = Field(..., max_length=280)
    tags: list[str] = Field(default_factory=list)
    sections: list[IngestSection] = Field(default_factory=list)
    summary: str | None = Field(default=None, max_length=500)

    @field_validator("created_at", mode="before")
    @classmethod
    def _coerce_created_at(cls, value: object) -> str:
        if isinstance(value, datetime):
            dt = value.astimezone(UTC)
            return time_utils.format_timestamp(dt)
        if value is None:
            return time_utils.format_timestamp(time_utils.now())
        if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
            dt = datetime(
                value.year,
                value.month,
                value.day,
                tzinfo=UTC,
            )
            return time_utils.format_timestamp(dt)
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return time_utils.format_timestamp(time_utils.now())
            normalized = candidate.replace("Z", "+00:00") if candidate.endswith("Z") else candidate
            try:
                dt = datetime.fromisoformat(normalized)
            except ValueError:
                return candidate
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return time_utils.format_timestamp(dt)
        return str(value)


def build_ingest_agent(
    config: AppConfig | None,
    *,
    model: str | None = None,
) -> Agent:
    """Construct a Pydantic AI Agent backed by Ollama with structured outputs."""
    ollama_config = build_ollama_config_from_mapping(config, model=model)
    return build_ollama_agent(
        ollama_config,
        system_prompt=INGEST_SYSTEM_PROMPT,
        output_type=IngestResult,
        name="aijournal-ingest",
    )


def ingest_with_agent(agent: Agent, *, source_path: Path, markdown: str) -> IngestResult:
    """Run the ingestion agent and return the structured output."""
    prompt = (
        "You will be given a Markdown document with optional front matter. "
        "Read it carefully and respond with JSON only.\n"
        f"SOURCE_PATH: {source_path}\n"
        "---BEGIN DOCUMENT---\n"
        f"{markdown}\n"
        "---END DOCUMENT---"
    )
    run = agent.run_sync(prompt)
    payload = run.output
    if isinstance(payload, IngestResult):
        return payload
    msg = "Agent did not return the expected structured payload"
    raise ValueError(msg)
