"""Functions orchestrating the `aijournal new` command."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import typer
from pydantic import BaseModel

from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.config_loader import use_fake_llm
from aijournal.common.context import RunContext, create_run_context
from aijournal.io.yaml_io import dump_yaml
from aijournal.utils import time as time_utils

FAKE_TIME_BLOCKS = [
    ("Morning focus", 9),
    ("Midday review", 12),
    ("Afternoon systems", 15),
    ("Evening reflection", 20),
]

FAKE_THEMES = [
    "deep work sprint",
    "planning checkpoint",
    "family logistics",
    "energy reset",
    "coaching prep",
    "writing sprint",
    "health baseline",
]

FAKE_PROJECTS = [
    "aijournal",
    "infra cleanup",
    "garden automation",
    "parenting playbook",
    "focus playlist",
    "writing pipeline",
]

FAKE_ACTIONS = [
    "Mapped blockers and sketched next three steps",
    "Clarified success criteria before touching code",
    "Reconciled notes from last retro",
    "Documented one insight per paragraph",
    "Turned vague worries into explicit tasks",
]

FAKE_REFLECTIONS = [
    "Noticed recurring tension around context switching",
    "Energy dipped after lunch but came back with a walk",
    "Family logistics feel smoother when blocked on Sundays",
    "Confidence spikes once the first win lands",
    "Need to protect two uninterrupted mornings",
]

FAKE_NEXT_STEPS = [
    "Block next session on the calendar",
    "Ping Jess for async review notes",
    "Move open todos into Things inbox",
    "Write a two-paragraph recap for future me",
    "Tidy prompt library before shipping",
]

FAKE_MOODS = ["steady", "energized", "calm", "curious", "stretched"]

FAKE_TAG_SETS = [
    ["focus", "planning"],
    ["reflection", "family"],
    ["health", "habits"],
    ["shipping", "systems"],
    ["writing", "learning"],
]

FAKE_MINUTES = [0, 5, 10, 15, 20, 30, 35, 40, 45, 50]


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


def _write_markdown_entry(path: Path, frontmatter: dict[str, Any], body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml_block = dump_yaml(frontmatter, sort_keys=False).strip()
    content = f"---\n{yaml_block}\n---\n"
    if body:
        content += f"\n{body.strip()}\n"
    else:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def _generate_fake_entries(
    count: int,
    override_tags: list[str] | None,
    seed: int | None,
    base: Path,
) -> tuple[int, int]:
    if count <= 0:
        return (0, 0)

    rng_seed = seed if seed is not None else int(time_utils.now().timestamp())
    rng = random.Random(rng_seed)
    base_dt = time_utils.now()
    base_day = base_dt.date()
    enforced_tags = list(override_tags or [])

    created = 0
    skipped = 0

    for idx in range(count):
        day = base_day - timedelta(days=idx)
        label, hour = rng.choice(FAKE_TIME_BLOCKS)
        minute = rng.choice(FAKE_MINUTES)
        created_dt = datetime(
            day.year,
            day.month,
            day.day,
            hour,
            minute,
            tzinfo=UTC,
        )
        theme = rng.choice(FAKE_THEMES)
        project = rng.choice(FAKE_PROJECTS)
        mood = rng.choice(FAKE_MOODS)
        action = rng.choice(FAKE_ACTIONS)
        reflection = rng.choice(FAKE_REFLECTIONS)
        next_step = rng.choice(FAKE_NEXT_STEPS)
        slug = f"{created_dt.strftime('%Y-%m-%d')}-{time_utils.slugify_title(theme)}-{time_utils.slugify_title(project)}"
        title = f"{label}: {theme.title()} ({project})"

        entry_path = _journal_path(base, created_dt, slug)
        if entry_path.exists():
            typer.echo(f"Skipping {entry_path} (already exists)")
            skipped += 1
            continue

        if enforced_tags:
            tags = enforced_tags
        else:
            auto_tags = set(rng.choice(FAKE_TAG_SETS))
            auto_tags.add(project.split()[0].lower())
            auto_tags.add(theme.split()[0].lower())
            tags = sorted(auto_tags)

        frontmatter = {
            "id": slug,
            "created_at": time_utils.format_timestamp(created_dt),
            "title": title,
            "tags": tags,
            "mood": mood,
            "projects": [project],
        }
        body = "\n\n".join(
            [
                f"{label} block stayed on {project}: {action}.",
                f"Felt {mood}; {reflection}.",
                f"Next: {next_step}.",
            ],
        )
        _write_markdown_entry(entry_path, frontmatter, body)
        typer.echo(str(entry_path))
        created += 1

    return created, skipped


class NewOptions(BaseModel):
    title: str | None
    tags: list[str] | None
    fake: int
    seed: int | None


@dataclass(slots=True)
class NewPrepared:
    mode: str  # "fake" or "entry"
    base: Path
    tags: list[str] | None
    fake_count: int
    seed: int | None
    title: str | None
    entry_path: Path | None
    frontmatter: dict[str, Any] | None


@dataclass(slots=True)
class NewResult:
    message: str
    entry_path: Path | None


def prepare_inputs(ctx: RunContext, options: NewOptions) -> NewPrepared:
    base = ctx.workspace
    if options.fake > 0:
        if options.title is not None:
            typer.secho(
                "Provide either a title or --fake, not both.",
                fg=typer.colors.RED,
                err=True,
            )
            ctx.emit(event="command_failed", reason="fake_with_title")
            raise typer.Exit(1)
        ctx.emit(event="prepare_summary", mode="fake", count=options.fake)
        return NewPrepared(
            mode="fake",
            base=base,
            tags=options.tags,
            fake_count=options.fake,
            seed=options.seed,
            title=None,
            entry_path=None,
            frontmatter=None,
        )

    if options.seed is not None:
        typer.secho("--seed is only valid together with --fake.", fg=typer.colors.RED, err=True)
        ctx.emit(event="command_failed", reason="seed_without_fake")
        raise typer.Exit(1)
    if not options.title:
        typer.secho("Title is required unless --fake is provided.", fg=typer.colors.RED, err=True)
        ctx.emit(event="command_failed", reason="missing_title")
        raise typer.Exit(1)

    current_time = time_utils.now()
    slug = f"{current_time.strftime('%Y-%m-%d')}-{time_utils.slugify_title(options.title)}"
    entry_path = _journal_path(base, current_time, slug)
    if entry_path.exists():
        typer.echo(f"Entry exists: {entry_path}")
        ctx.emit(event="command_failed", reason="entry_exists", path=str(entry_path))
        raise typer.Exit(1)

    frontmatter = {
        "id": slug,
        "created_at": time_utils.format_timestamp(current_time),
        "title": options.title,
        "tags": options.tags or [],
    }
    ctx.emit(event="prepare_summary", mode="entry", title=options.title)
    return NewPrepared(
        mode="entry",
        base=base,
        tags=options.tags,
        fake_count=0,
        seed=None,
        title=options.title,
        entry_path=entry_path,
        frontmatter=frontmatter,
    )


def invoke_pipeline(ctx: RunContext, prepared: NewPrepared) -> NewResult:
    if prepared.mode == "fake":
        created, skipped = _generate_fake_entries(
            prepared.fake_count,
            prepared.tags,
            prepared.seed,
            prepared.base,
        )
        summary = f"Generated {created} fake entr{'y' if created == 1 else 'ies'}"
        if skipped:
            summary += f" ({skipped} skipped)"
        ctx.emit(
            event="pipeline_complete",
            mode="fake",
            created=created,
            skipped=skipped,
        )
        return NewResult(message=summary, entry_path=None)

    assert prepared.entry_path is not None
    assert prepared.frontmatter is not None
    _write_markdown_entry(prepared.entry_path, prepared.frontmatter)
    ctx.emit(
        event="pipeline_complete",
        mode="entry",
        path=str(prepared.entry_path),
    )
    return NewResult(message=str(prepared.entry_path), entry_path=prepared.entry_path)


def persist_output(ctx: RunContext, result: NewResult) -> None:
    del ctx
    typer.echo(result.message)


def run_new_command(ctx: RunContext, options: NewOptions) -> None:
    run_command_pipeline(
        ctx,
        options,
        prepare_inputs=prepare_inputs,
        invoke_pipeline=invoke_pipeline,
        persist_output=persist_output,
    )


def run_new(
    title: str | None,
    tags: list[str] | None,
    fake: int,
    seed: int | None,
    workspace: Path | None = None,
) -> None:
    workspace = workspace or Path.cwd()
    ctx = create_run_context(
        command="ops.dev.new",
        workspace=workspace,
        config={},
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )
    options = NewOptions(title=title, tags=tags, fake=fake, seed=seed)
    run_new_command(ctx, options)
