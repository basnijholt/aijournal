"""Functions orchestrating the `aijournal init` command."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from aijournal.common.command_runner import run_command_pipeline
from aijournal.common.config_loader import use_fake_llm
from aijournal.common.context import RunContext, create_run_context
from aijournal.utils.paths import (
    AUTHORITATIVE_DIRS,
    DERIVED_DIRS,
    ensure_directories,
    ensure_gitkeep_files,
    ensure_seed_files,
)


class InitOptions(BaseModel):
    path: Path


@dataclass(slots=True)
class InitPrepared:
    base: Path


@dataclass(slots=True)
class InitResult:
    base: Path
    created_dirs: int
    created_files: int
    total_dirs: int
    total_files: int


def prepare_inputs(ctx: RunContext, options: InitOptions) -> InitPrepared:
    base = options.path
    base.mkdir(parents=True, exist_ok=True)
    ctx.emit(event="prepare_summary", path=str(base))
    return InitPrepared(base=base)


def invoke_pipeline(ctx: RunContext, prepared: InitPrepared) -> InitResult:
    dir_sets = (AUTHORITATIVE_DIRS, DERIVED_DIRS)
    created_dirs = 0
    total_dirs = 0
    for rels in dir_sets:
        created, total = ensure_directories(prepared.base, rels)
        created_dirs += created
        total_dirs += total

    unique_dirs: list[str] = []
    seen: set[str] = set()
    for rels in dir_sets:
        for rel in rels:
            if rel in seen:
                continue
            seen.add(rel)
            unique_dirs.append(rel)

    gitkeep_created, gitkeep_total = ensure_gitkeep_files(prepared.base, unique_dirs)

    seed_created, seed_total = ensure_seed_files(prepared.base)
    created_files = gitkeep_created + seed_created
    total_files = gitkeep_total + seed_total

    ctx.emit(
        event="pipeline_complete",
        created_dirs=created_dirs,
        created_files=created_files,
    )
    return InitResult(
        base=prepared.base,
        created_dirs=created_dirs,
        created_files=created_files,
        total_dirs=total_dirs,
        total_files=total_files,
    )


def persist_output(ctx: RunContext, result: InitResult) -> str:
    del ctx
    already_dirs = result.total_dirs - result.created_dirs
    already_files = result.total_files - result.created_files
    return (
        f"Created {result.created_dirs} directories and {result.created_files} files under {result.base}. "
        f"Already present: {already_dirs} directories and {already_files} files."
    )


def run_init_command(ctx: RunContext, options: InitOptions) -> str:
    return run_command_pipeline(
        ctx,
        options,
        prepare_inputs=prepare_inputs,
        invoke_pipeline=invoke_pipeline,
        persist_output=persist_output,
    )


def run_init(path: Path | None = None) -> str:
    """Initialize an aijournal workspace.

    Args:
        path: Workspace directory (defaults to current directory)

    Returns:
        Status message describing what was created

    """
    workspace = path or Path.cwd()
    workspace.mkdir(parents=True, exist_ok=True)
    ctx = create_run_context(
        command="init",
        workspace=workspace,
        config={},
        use_fake_llm=use_fake_llm(),
        trace=False,
        verbose_json=False,
    )
    options = InitOptions(path=workspace)
    return run_init_command(ctx, options)
