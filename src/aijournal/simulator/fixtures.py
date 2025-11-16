"""Deterministic fixtures for exercising capture with human-like inputs."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from aijournal.commands.init import run_init

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(slots=True)
class FixtureEntry:
    """Represents a single simulated input file."""

    label: str
    path: Path
    description: str


@dataclass(slots=True)
class FixtureWorkspace:
    """Workspace populated with deterministic human-style entries."""

    root: Path
    input_dir: Path
    entries: list[FixtureEntry]


def build_fixture_workspace(base_dir: Path | None = None) -> FixtureWorkspace:
    """Create a temporary workspace pre-populated with messy entries."""
    if base_dir is None:
        tmp_root = Path(tempfile.mkdtemp(prefix="aijournal-sim-"))
    else:
        tmp_root = base_dir
        tmp_root.mkdir(parents=True, exist_ok=True)

    run_init(tmp_root)

    input_dir = tmp_root / "simulator" / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)

    entries: list[FixtureEntry] = []
    for definition in _fixture_definitions():
        file_path = input_dir / definition["filename"]
        file_path.write_text(definition["content"], encoding="utf-8")
        entries.append(
            FixtureEntry(
                label=definition["label"],
                path=file_path,
                description=definition["description"],
            ),
        )

    return FixtureWorkspace(root=tmp_root, input_dir=input_dir, entries=entries)


def _fixture_definitions() -> Iterable[dict[str, str]]:
    """Yield deterministic entry definitions spanning multiple formats/dates."""
    return [
        {
            "label": "good_yaml",
            "filename": "good-yaml.md",
            "description": "Healthy YAML front matter with tags and mood.",
            "content": (
                "---\n"
                "id: focus-reset\n"
                "created_at: 2025-01-05T09:00:00Z\n"
                "title: Focus Reset\n"
                "tags:\n"
                "  - focus\n"
                "  - wins\n"
                "projects: [deep-work]\n"
                "mood: steady\n"
                'summary: "Captured a solid workflow"\n'
                "---\n\n"
                "# Morning Focus Reset\n"
                "Wrote a tight plan and stuck to it.\n"
            ),
        },
        {
            "label": "toml_front_matter",
            "filename": "toml-entry.md",
            "description": "Hugo-style TOML block with arrays and mixed casing.",
            "content": (
                "+++\n"
                'id = "toml-routine"\n'
                'date = "2025-01-04T10:00:00Z"\n'
                'title = "TOML Routine"\n'
                'tags = ["toml", "routines"]\n'
                'projects = ["ops", "health"]\n'
                "+++\n\n"
                "## Daily Routine Tweaks\n"
                "Documented schedule variations for the week.\n"
            ),
        },
        {
            "label": "broken_front_matter",
            "filename": "broken-front-matter.md",
            "description": "Partial YAML block with missing quotes to trigger tolerant parser.",
            "content": (
                "---\n"
                'title: "Broken Front Matter\n'
                "created_at: 2025-01-03T07:30:00Z\n"
                "summary: Missing closing quote forces tolerant path\n"
                "---\n\n"
                "Body still exists even though the header is malformed.\n"
            ),
        },
        {
            "label": "no_front_matter",
            "filename": "body-date.md",
            "description": "Body-only entry that mentions the date inline.",
            "content": (
                "Date: Jan 2, 2025\n\n"
                "# Freeform Reflection\n"
                "Recorded notes without any front matter at all.\n"
            ),
        },
        {
            "label": "messy_markdown",
            "filename": "messy.md",
            "description": "Malformed markdown with stray bullets and minimal content.",
            "content": (
                "---\n"
                "id: messy-notes\n"
                "created_at: 2025-01-01\n"
                "projects: [messy]\n"
                "---\n\n"
                "- bullet starts\n"
                "## but no spacing\n"
                "> quote without closing\n"
            ),
        },
        {
            "label": "retro_focus",
            "filename": "retro-focus.md",
            "description": "Older entry to trigger index history and persona context.",
            "content": (
                "---\n"
                "id: retro-focus\n"
                "created_at: 2024-12-28T08:00:00Z\n"
                "title: Retro Focus Log\n"
                "tags: [retro, focus]\n"
                "projects: [legacy]\n"
                "source_type: blog\n"
                "---\n\n"
                "Kept a short focus journal during the holiday wind-down.\n"
            ),
        },
        {
            "label": "duplicate_slug",
            "filename": "duplicate-slug.md",
            "description": "Entry that reuses an ID to exercise slug collision handling.",
            "content": (
                "---\n"
                "id: focus-reset\n"
                "created_at: 2024-12-30T09:00:00Z\n"
                "title: Focus Reset Follow-up\n"
                "tags: [focus]\n"
                "---\n\n"
                "Documented how the focus reset went a few days later.\n"
            ),
        },
        {
            "label": "weekly-planning",
            "filename": "weekly-planning.md",
            "description": "Long-form entry with bullet lists and headings.",
            "content": (
                "---\n"
                "id: weekly-planning\n"
                "created_at: 2024-12-31T21:00:00Z\n"
                "title: Weekly Planning\n"
                "tags: [planning]\n"
                "projects: [ops]\n"
                'summary: "Outlined planning rituals for the new year"\n'
                "---\n\n"
                "# Planning Rituals\n"
                "- Capture wins\n"
                "- Note risks\n"
                "\n## Notes\nDocumented how conflicting goals show up.\n"
            ),
        },
        {
            "label": "history-anchor",
            "filename": "history-anchor.md",
            "description": "Entry far enough back to exercise history windows and pack exports.",
            "content": (
                "---\n"
                "id: history-anchor\n"
                "created_at: 2024-12-27T07:30:00Z\n"
                "title: History Anchor\n"
                "tags: [history]\n"
                "---\n\n"
                "Captures a snapshot older than a week to ensure index tails rebuild.\n"
            ),
        },
    ]
