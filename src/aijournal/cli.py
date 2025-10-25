"""Typer CLI entrypoint for aijournal."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

import typer
import yaml


app = typer.Typer(help="Local-first personal journal utilities.")


@app.callback()
def main() -> None:
    """aijournal command-line interface."""

    # Intentionally empty; commands provide functionality.
    return None


AUTHORITATIVE_DIRS = (
    "config",
    "profile",
    "data",
    "data/journal",
    "data/normalized",
    "prompts",
)

DERIVED_DIRS = (
    "derived",
    "derived/summaries",
    "derived/microfacts",
    "derived/profile_suggestions",
    "derived/interviews",
    "derived/advice",
    "derived/index",
)

SEED_FILES = {
    "config/config.yaml": """model: \"llama3.1:8b-instruct\"\ntemperature: 0.2\nseed: 42\npaths:\n  data: \"data\"\n  profile: \"profile\"\n  derived: \"derived\"\n  prompts: \"prompts\"\nimpact_weights:\n  values_goals: 1.5\n  decision_style: 1.3\n  affect_energy: 1.2\n  traits: 1.0\n  social: 0.9\nadvisor:\n  max_recos: 3\n  include_risks: true\n""",
    "profile/self_profile.yaml": """traits:\n  big_five:\n    openness: {score: 0.74, method: self_report, user_verified: true}\n    conscientiousness: {score: 0.68, method: inferred}\n    extraversion: {score: 0.42, method: self_report}\n    agreeableness: {score: 0.61, method: inferred}\n    neuroticism: {score: 0.33, method: self_report}\n  regulatory_focus: {promotion: 0.7, prevention: 0.3}\n  risk_tolerance: {domain: \"career\", level: \"medium-high\"}\n  time_horizon: {preferred: \"long\", evidence: [\"2024_l2_...\"]}\n  review_after_days: 180\n\nvalues_motivations:\n  schwartz_top5: [\"Self-Direction\", \"Achievement\", \"Universalism\", \"Benevolence\", \"Security\"]\n  sdt: {autonomy: 0.8, competence: 0.7, relatedness: 0.6}\n  drivers:\n    - value: \"Mastery over tools & systems\"\n      method: inferred\n      confidence: 0.8\n  review_after_days: 120\n\ngoals:\n  short_term:\n    - value: \"Ship personal agent MVP\"\n      why: \"reduce friction\"\n      krs: [\"CLI usable\", \"context pack <1800t\"]\n      review_after_days: 30\n  long_term:\n    - value: \"Work-life consistency with twins\"\n      krs: [\"2 evenings/week protected\"]\n      review_after_days: 90\n  anti_goals:\n    - value: \"No late-night production firefighting as a norm\"\n      reason: \"family/health\"\n\ndecision_style:\n  default: {speed_vs_quality: \"quality\", satisficer_vs_maximizer: \"bounded_maximizer\"}\n  implementation_intentions:\n    - if: \"Feeling anxious before presentations\"\n      then: \"Run checklist + 10-min rehearsal\"\n      evidence: [\"2021-04-12_l1\"]\n\naffect_energy:\n  energy_map: {morning: \"high\", afternoon: \"medium\", evening: \"low\"}\n  stressors: [\"ambiguous deadlines\", \"noisy environment\"]\n  coping_strategies: [\"walks\", \"time-boxing\", \"no email after 18:00\"]\n\nsocial:\n  relationships:\n    - person: \"Jess\"\n      role: \"coworker\"\n      notes: \"great feedback partner\"\n      boundary: \"no pings after 18:00\"\n\nboundaries_ethics:\n  red_lines: [\"No sharing private family data\", \"No health advice beyond guidelines\"]\n\ncoaching_prefs:\n  tone: \"direct, warm\"\n  depth: \"concrete first, theory second\"\n  probing: {max_questions: 2, prefer: \"yes/no + one short open follow-up\"}\n""",
    "profile/claims.yaml": """claims: []\n""",
}


def _now() -> datetime:
    """Return the current UTC time; separated for easy monkeypatching in tests."""

    return datetime.now(tz=timezone.utc)


def _slugify_title(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "entry"


def _format_timestamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _journal_path(base: Path, dt: datetime, slug: str) -> Path:
    return (
        base
        / "data"
        / "journal"
        / dt.strftime("%Y")
        / dt.strftime("%m")
        / dt.strftime("%d")
        / f"{slug}.md"
    )


def _ensure_dirs(base: Path, rel_paths: Iterable[str]) -> tuple[int, int]:
    paths = tuple(rel_paths)
    created = 0
    for rel in paths:
        target = base / rel
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
            created += 1
        else:
            target.mkdir(parents=True, exist_ok=True)
    return created, len(paths)


def _ensure_files(base: Path) -> tuple[int, int]:
    created = 0
    for rel, content in SEED_FILES.items():
        target = base / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content.rstrip() + "\n", encoding="utf-8")
        created += 1
    return created, len(SEED_FILES)


@app.command()
def init(
    path: Optional[Path] = typer.Option(
        None,
        "--path",
        "-p",
        help="Directory to initialize (defaults to current working directory).",
    ),
) -> None:
    """Initialize the local aijournal layout."""

    base = path or Path.cwd()
    base.mkdir(parents=True, exist_ok=True)

    dir_sets = (AUTHORITATIVE_DIRS, DERIVED_DIRS)
    created_dirs = 0
    total_dirs = 0
    for rels in dir_sets:
        created, total = _ensure_dirs(base, rels)
        created_dirs += created
        total_dirs += total

    created_files, total_files = _ensure_files(base)

    already_dirs = total_dirs - created_dirs
    already_files = total_files - created_files

    summary = (
        f"Created {created_dirs} directories and {created_files} files under {base}. "
        f"Already present: {already_dirs} directories and {already_files} files."
    )
    typer.echo(summary)


@app.command()
def new(
    title: str = typer.Argument(..., help="Title for the journal entry."),
    tags: Optional[List[str]] = typer.Option(
        None,
        "--tags",
        "-t",
        help="Tag to attach to the entry (repeatable).",
    ),
) -> None:
    """Create a new journal entry with YAML frontmatter."""

    now = _now()
    slug = f"{now.strftime('%Y-%m-%d')}-{_slugify_title(title)}"
    entry_path = _journal_path(Path.cwd(), now, slug)

    if entry_path.exists():
        typer.echo(f"Entry exists: {entry_path}")
        raise typer.Exit(1)

    entry_path.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = {
        "id": slug,
        "created_at": _format_timestamp(now),
        "title": title,
        "tags": tags or [],
    }

    yaml_block = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    body = "---\n" + yaml_block + "\n---\n\n"
    entry_path.write_text(body, encoding="utf-8")

    typer.echo(str(entry_path))
