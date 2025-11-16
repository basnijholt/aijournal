"""Helpers for loading and validating daily summary artifacts."""

from __future__ import annotations

from datetime import date as _date
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, overload

from aijournal.domain.facts import DailySummary
from aijournal.io.artifacts import load_artifact_data

if TYPE_CHECKING:
    from collections.abc import Iterable

    from aijournal.common.app_config import AppConfig


class SummaryNotFoundError(FileNotFoundError):
    """Raised when a required day-level summary artifact is missing."""

    def __init__(self, date: str, path: Path) -> None:
        self.date = date
        self.path = path
        self._remediation = f"uv run aijournal ops pipeline summarize --date {date}"
        message = (
            f"Daily summary for {date} not found.\n"
            f"Expected path: {path}\n\n"
            f"Run this to generate it:\n  {self._remediation}"
        )
        super().__init__(message)

    @property
    def remediation(self) -> str:
        return self._remediation


def summary_artifact_path(workspace: Path, config: AppConfig, day: str) -> Path:
    """Return the absolute path to `derived/summaries/<day>.yaml`."""
    derived = Path(config.paths.derived)
    if not derived.is_absolute():
        derived = workspace / derived
    return derived / "summaries" / f"{day}.yaml"


@overload
def load_daily_summary(
    workspace: Path,
    config: AppConfig,
    day: str,
    *,
    required: Literal[True] = True,
) -> DailySummary: ...


@overload
def load_daily_summary(
    workspace: Path,
    config: AppConfig,
    day: str,
    *,
    required: Literal[False],
) -> DailySummary | None: ...


def load_daily_summary(
    workspace: Path,
    config: AppConfig,
    day: str,
    *,
    required: bool = True,
) -> DailySummary | None:
    """Load the `DailySummary` artifact for ``day``.

    Args:
        workspace: Workspace root directory.
        config: Application configuration (provides `paths.derived`).
        day: Target date in ``YYYY-MM-DD`` format.
        required: When ``True`` (default) raise :class:`SummaryNotFoundError`
            if the summary is missing. When ``False`` return ``None`` instead.

    """
    path = summary_artifact_path(workspace, config, day)
    if not path.exists():
        if required:
            raise SummaryNotFoundError(day, path)
        return None
    return load_artifact_data(path, DailySummary)


def load_summary_window(
    workspace: Path,
    config: AppConfig,
    *,
    anchor_day: str,
    lookback_days: int = 7,
    include_anchor: bool = True,
) -> list[tuple[str, DailySummary]]:
    """Load summaries for ``anchor_day`` and the preceding ``lookback_days``.

    Missing historical summaries are skipped silently; callers can decide
    whether that warrants a warning. The anchor day is loaded only when
    ``include_anchor`` is True.
    """
    anchor = _date.fromisoformat(anchor_day)
    offsets: Iterable[int]
    offsets = range(lookback_days, -1, -1) if include_anchor else range(lookback_days, 0, -1)

    results: list[tuple[str, DailySummary]] = []
    for offset in offsets:
        target = anchor - timedelta(days=offset)
        date_str = target.isoformat()
        summary: DailySummary | None
        if date_str == anchor_day:
            summary = load_daily_summary(
                workspace,
                config,
                date_str,
                required=True,
            )
        else:
            summary = load_daily_summary(
                workspace,
                config,
                date_str,
                required=False,
            )
        if summary is not None:
            results.append((date_str, summary))
    return results


__all__ = [
    "SummaryNotFoundError",
    "load_daily_summary",
    "load_summary_window",
    "summary_artifact_path",
]
