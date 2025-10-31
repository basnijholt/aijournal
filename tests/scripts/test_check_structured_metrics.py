from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_structured_metrics.py"


def _write_metrics(path: Path, entries: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")


def test_check_structured_metrics_passes(tmp_path: Path) -> None:
    metrics_path = tmp_path / "metrics.jsonl"
    _write_metrics(
        metrics_path,
        [
            {"repair_attempts": 0, "coercion_count": 1},
            {"repair_attempts": 1, "coercion_count": 2},
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--path",
            str(metrics_path),
            "--max-repair-rate",
            "0.6",
            "--max-avg-coercions",
            "3.0",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_check_structured_metrics_fails_when_exceeding_threshold(tmp_path: Path) -> None:
    metrics_path = tmp_path / "metrics.jsonl"
    _write_metrics(
        metrics_path,
        [
            {"repair_attempts": 5, "coercion_count": 20},
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--path",
            str(metrics_path),
            "--max-repair-rate",
            "0.1",
            "--max-avg-coercions",
            "3.0",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Repair rate" in result.stdout
