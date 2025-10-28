"""Functions orchestrating the `aijournal init` command."""

from __future__ import annotations

from pathlib import Path

from aijournal.utils.paths import (
    AUTHORITATIVE_DIRS,
    DERIVED_DIRS,
    ensure_directories,
    ensure_seed_files,
)


def run_init(path: Path | None = None) -> str:
    """Bootstrap the standard project layout and return a summary message."""
    base = path or Path.cwd()
    base.mkdir(parents=True, exist_ok=True)

    dir_sets = (AUTHORITATIVE_DIRS, DERIVED_DIRS)
    created_dirs = 0
    total_dirs = 0
    for rels in dir_sets:
        created, total = ensure_directories(base, rels)
        created_dirs += created
        total_dirs += total

    created_files, total_files = ensure_seed_files(base)

    already_dirs = total_dirs - created_dirs
    already_files = total_files - created_files

    return (
        f"Created {created_dirs} directories and {created_files} files under {base}. "
        f"Already present: {already_dirs} directories and {already_files} files."
    )
