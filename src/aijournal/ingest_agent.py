"""Structured ingestion helpers backed by agno + Ollama."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agno.agent import Agent
from agno.models.ollama import Ollama
from pydantic import BaseModel, Field

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


class IngestSection(BaseModel):
    """Structured representation of a heading block."""

    heading: str = Field(..., max_length=200)
    level: int = Field(default=2, ge=1, le=6)
    summary: str | None = Field(default=None, max_length=320)


class IngestResult(BaseModel):
    """Structured output returned by the ingestion agent."""

    entry_id: str | None = Field(default=None, description="Slug or identifier for this entry")
    created_at: datetime
    title: str = Field(..., max_length=280)
    tags: list[str] = Field(default_factory=list)
    sections: list[IngestSection] = Field(default_factory=list)
    summary: str | None = Field(default=None, max_length=500)


@dataclass(frozen=True)
class AgentSettings:
    """Runtime settings for the ingestion agent."""

    model: str
    host: str | None
    temperature: float | None
    seed: int | None


def build_ingest_agent(settings: AgentSettings) -> Agent:
    """Construct an agno Agent backed by Ollama with structured outputs."""
    options: dict[str, float | int] = {}
    if settings.temperature is not None:
        options["temperature"] = float(settings.temperature)
    if settings.seed is not None:
        options["seed"] = int(settings.seed)

    return Agent(
        name="aijournal-ingest",
        instructions=INGEST_SYSTEM_PROMPT,
        model=Ollama(
            id=settings.model,
            host=settings.host,
            options=options or None,
        ),
        output_schema=IngestResult,
        add_datetime_to_context=True,
        telemetry=False,
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
    run = agent.run(prompt)
    content = run.content
    if isinstance(content, IngestResult):
        return content
    msg = "Agent did not return the expected structured payload"
    raise ValueError(msg)


__all__ = [
    "AgentSettings",
    "IngestResult",
    "IngestSection",
    "build_ingest_agent",
    "ingest_with_agent",
]
