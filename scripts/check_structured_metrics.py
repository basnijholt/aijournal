#!/usr/bin/env python3
"""Check aggregated structured LLM metrics against defined thresholds."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass
class MetricsSummary:
    calls: int = 0
    repair_attempts: int = 0
    coercions: int = 0

    def __iadd__(self, other: MetricsSummary) -> Self:  # pragma: no cover - convenience
        self.calls += other.calls
        self.repair_attempts += other.repair_attempts
        self.coercions += other.coercions
        return self


def _load_metrics(path: Path) -> Iterable[dict[str, object]]:
    if not path.exists():
        return []
    entries: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:  # pragma: no cover - defensive
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def _summarise(entries: Iterable[dict[str, object]]) -> MetricsSummary:
    summary = MetricsSummary()
    for item in entries:
        summary.calls += 1
        summary.repair_attempts += int(item.get("repair_attempts", 0))
        summary.coercions += int(item.get("coercion_count", 0))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate structured LLM telemetry metrics")
    parser.add_argument(
        "--path",
        type=Path,
        default=Path("derived/logs/structured_metrics.jsonl"),
        help="Metrics file to evaluate (default: derived/logs/structured_metrics.jsonl)",
    )
    parser.add_argument(
        "--max-repair-rate",
        type=float,
        default=0.10,
        help="Maximum allowed repair attempts per call (default: 0.10)",
    )
    parser.add_argument(
        "--max-avg-coercions",
        type=float,
        default=3.0,
        help="Maximum allowed average coercions per call (default: 3.0)",
    )
    args = parser.parse_args()

    entries = list(_load_metrics(args.path))
    if not entries:
        print(f"No metrics found at {args.path}; skipping thresholds.")
        return 0

    summary = _summarise(entries)
    if summary.calls == 0:
        print("No structured calls recorded; skipping thresholds.")
        return 0

    repair_rate = summary.repair_attempts / summary.calls
    avg_coercions = summary.coercions / summary.calls

    ok = True
    if repair_rate > args.max_repair_rate:
        print(f"Repair rate {repair_rate:.3f} exceeded threshold {args.max_repair_rate:.3f}")
        ok = False
    if avg_coercions > args.max_avg_coercions:
        print(
            f"Average coercions {avg_coercions:.3f} exceeded threshold {args.max_avg_coercions:.3f}",
        )
        ok = False

    print(
        f"Metrics: calls={summary.calls} repairs={summary.repair_attempts} "
        f"coercions={summary.coercions} repair_rate={repair_rate:.3f} "
        f"avg_coercions={avg_coercions:.3f}",
    )

    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover - script entry point
    sys.exit(main())
