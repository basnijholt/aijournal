"""Pipeline helpers for assembling pack bundles."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from aijournal.domain.packs import PackBundle, PackEntry, PackMeta, TrimmedFile
from aijournal.utils import time as time_utils

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

ROLE_ORDER = [
    "persona_core",
    "profile",
    "claims",
    "config",
    "prompt",
    "normalized",
    "summaries",
    "microfacts",
    "consolidated_microfacts",
    "advice",
    "profile_updates",
    "journal_raw",
]

TRIM_PRIORITY = [
    "journal_raw",
    "prompt",
    "config",
    "advice",
    "profile_updates",
    "microfacts",
    "consolidated_microfacts",
    "summaries",
    "normalized",
    "profile",
    "claims",
]


class PackAssemblyError(RuntimeError):
    """Raised when required pack artifacts are missing."""


def _add_path(
    entries: list[tuple[str, Path, int]],
    role: str,
    path: Path,
    *,
    required: bool = False,
    day_index: int = 0,
) -> None:
    if path.is_file():
        entries.append((role, path, day_index))
    elif required:
        msg = f"Missing required file {path}"
        raise PackAssemblyError(msg)


def _add_dir(
    entries: list[tuple[str, Path, int]],
    role: str,
    directory: Path,
    *,
    required: bool = False,
    pattern: str | None = None,
    recursive: bool = False,
    day_index: int = 0,
) -> None:
    if not directory.exists():
        if required:
            msg = f"Missing required files under {directory}"
            raise PackAssemblyError(msg)
        return
    if recursive:
        files = sorted(p for p in directory.rglob("*") if p.is_file())
    elif pattern:
        files = sorted(directory.glob(pattern))
    else:
        files = sorted(p for p in directory.iterdir() if p.is_file())
    if not files and required:
        msg = f"Missing required files under {directory}"
        raise PackAssemblyError(msg)
    for file in files:
        entries.append((role, file, day_index))


def _add_day_artifacts(
    entries: list[tuple[str, Path, int]],
    root: Path,
    day: str,
    day_index: int,
    *,
    include_normalized: bool,
    include_summary: bool,
    include_microfacts: bool,
    include_raw: bool,
    required_core: bool,
) -> None:
    if include_normalized:
        normalized_dir = root / "data" / "normalized" / day
        _add_dir(
            entries,
            "normalized",
            normalized_dir,
            required=required_core,
            pattern="*.yaml",
            day_index=day_index,
        )
    if include_summary:
        summary_path = root / "derived" / "summaries" / f"{day}.yaml"
        _add_path(entries, "summaries", summary_path, day_index=day_index)
    if include_microfacts:
        microfacts_path = root / "derived" / "microfacts" / f"{day}.yaml"
        _add_path(entries, "microfacts", microfacts_path, day_index=day_index)
    if include_raw:
        year, month, day_part = day.split("-")
        journal_dir = root / "data" / "journal" / year / month / day_part
        _add_dir(entries, "journal_raw", journal_dir, pattern="*.md", day_index=day_index)


def collect_pack_entries(
    root: Path,
    level: str,
    date: str,
    history_days: int,
) -> list[tuple[str, Path]]:
    level = level.upper()
    if level not in {"L1", "L2", "L3", "L4"}:
        msg = f"Unsupported level {level}"
        raise ValueError(msg)

    entries: list[tuple[str, Path, int]] = []
    _add_path(
        entries,
        "persona_core",
        root / "derived" / "persona" / "persona_core.yaml",
        required=True,
    )

    if level == "L1":
        return _sorted_entries(entries)

    _add_path(entries, "profile", root / "profile" / "self_profile.yaml", required=True)
    _add_path(entries, "claims", root / "profile" / "claims.yaml", required=True)

    if level in {"L3", "L4"}:
        consolidated = root / "derived" / "microfacts" / "consolidated.yaml"
        _add_path(entries, "consolidated_microfacts", consolidated)

    include_history = level == "L4"
    if level in {"L2", "L3", "L4"}:
        anchor = datetime.fromisoformat(date)
        day_offsets: list[tuple[str, int]] = [(date, 0)]
        if include_history and history_days > 0:
            for offset in range(1, history_days + 1):
                prior = (anchor - timedelta(days=offset)).strftime("%Y-%m-%d")
                day_offsets.append((prior, offset))

        for day_value, idx in day_offsets:
            _add_day_artifacts(
                entries,
                root,
                day_value,
                idx,
                include_normalized=True,
                include_summary=True,
                include_microfacts=True,
                include_raw=include_history,
                required_core=idx == 0,
            )

        if level == "L2":
            for offset in range(1, 7):
                prior = (anchor - timedelta(days=offset)).strftime("%Y-%m-%d")
                _add_day_artifacts(
                    entries,
                    root,
                    prior,
                    offset,
                    include_normalized=False,
                    include_summary=True,
                    include_microfacts=True,
                    include_raw=False,
                    required_core=False,
                )

    if level in {"L3", "L4"}:
        advice_dir = root / "derived" / "advice" / date
        _add_dir(entries, "advice", advice_dir, pattern="*.yaml")
        pending_dir = root / "derived" / "pending" / "profile_updates"
        candidates = sorted(pending_dir.glob(f"{date}*.yaml"))
        if candidates:
            _add_path(entries, "profile_updates", candidates[-1])

    if level == "L4":
        prompts_dir = root / "prompts"
        _add_dir(entries, "prompt", prompts_dir, pattern="*.md", recursive=True)
        _add_path(entries, "config", root / "config.yaml")

    return _sorted_entries(entries)


def _sorted_entries(entries: Iterable[tuple[str, Path, int]]) -> list[tuple[str, Path]]:
    role_rank = {role: idx for idx, role in enumerate(ROLE_ORDER)}
    sorted_entries = sorted(
        entries,
        key=lambda item: (role_rank.get(item[0], len(ROLE_ORDER)), item[2], str(item[1])),
    )
    return [(role, path) for role, path, _ in sorted_entries]


def trim_entries(
    entries: list[PackEntry],
    budget: int,
    trimmed: list[TrimmedFile],
) -> None:
    def total_tokens() -> int:
        return sum(entry.tokens for entry in entries)

    if total_tokens() <= budget:
        return

    for role in TRIM_PRIORITY:
        for entry in entries:
            if entry.role == role and entry.tokens > 0:
                trimmed.append(TrimmedFile(role=role, path=entry.path))
                entry.content = "(trimmed due to token budget)"
                entry.tokens = 0
                if total_tokens() <= budget:
                    return


def build_pack_payload(
    entries: list[PackEntry],
    level: str,
    date: str,
    trimmed: list[TrimmedFile],
    total_tokens: int,
    max_tokens: int,
) -> PackBundle:
    meta = PackMeta(
        total_tokens=total_tokens,
        max_tokens=max_tokens,
        trimmed=trimmed,
        generated_at=time_utils.format_timestamp(time_utils.now()),
    )
    return PackBundle(level=level, date=date, files=entries, meta=meta)
