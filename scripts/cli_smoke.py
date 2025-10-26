"""Simple harness to exercise key `aijournal` CLI flows."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class CommandSpec:
    """Describes a CLI command to execute via uv."""

    name: str
    args: list[str]
    expect_success: bool = True
    description: str | None = None


def _build_specs(base_day: str) -> list[CommandSpec]:
    return [
        CommandSpec(
            name="persona status (pre-build)",
            args=["aijournal", "persona", "status"],
            expect_success=False,
            description="Show the failure emitted when persona_core.yaml is absent.",
        ),
        CommandSpec(
            name="persona build",
            args=["aijournal", "persona", "build"],
            description="Regenerate persona core before other commands rely on it.",
        ),
        CommandSpec(
            name="persona status (post-build)",
            args=["aijournal", "persona", "status"],
            description="Confirm persona core is now considered fresh.",
        ),
        CommandSpec(
            name="profile status",
            args=["aijournal", "profile", "status"],
            description="Rank facets/claims needing attention.",
        ),
        CommandSpec(
            name="ollama health",
            args=["aijournal", "ollama", "health"],
            description="Validate the fake Ollama probe wiring.",
        ),
        CommandSpec(
            name="pack L1",
            args=["aijournal", "pack", "--level", "L1", "--dry-run"],
            description="Ensure persona core alone can be packaged.",
        ),
        CommandSpec(
            name="pack L2",
            args=[
                "aijournal",
                "pack",
                "--level",
                "L2",
                "--date",
                base_day,
                "--dry-run",
            ],
            description="Include latest normalized entries/summaries in an L2 pack.",
        ),
        CommandSpec(
            name="pack L3",
            args=[
                "aijournal",
                "pack",
                "--level",
                "L3",
                "--date",
                base_day,
                "--dry-run",
            ],
            description="Exercise the extended profile layer.",
        ),
        CommandSpec(
            name="pack L4",
            args=[
                "aijournal",
                "pack",
                "--level",
                "L4",
                "--date",
                base_day,
                "--history-days",
                "1",
                "--dry-run",
            ],
            description="Layer prompts/config/raw history under the selected date.",
        ),
        CommandSpec(
            name="summarize",
            args=[
                "aijournal",
                "summarize",
                "--date",
                base_day,
                "--progress",
            ],
            description="Summarize normalized entries for the base day.",
        ),
        CommandSpec(
            name="facts",
            args=[
                "aijournal",
                "facts",
                "--date",
                base_day,
                "--progress",
            ],
            description="Extract micro-facts and consolidation preview.",
        ),
        CommandSpec(
            name="profile suggest",
            args=[
                "aijournal",
                "profile",
                "suggest",
                "--date",
                base_day,
                "--progress",
            ],
            description="Propose profile updates via structured responses.",
        ),
        CommandSpec(
            name="characterize",
            args=[
                "aijournal",
                "characterize",
                "--date",
                base_day,
                "--progress",
            ],
            description="Generate pending profile updates for review.",
        ),
        CommandSpec(
            name="index rebuild",
            args=[
                "aijournal",
                "index",
                "rebuild",
                "--since",
                base_day,
                "--limit",
                "25",
            ],
            description="Rebuild retrieval assets from recent normalized entries.",
        ),
        CommandSpec(
            name="index tail",
            args=[
                "aijournal",
                "index",
                "tail",
                "--since",
                base_day,
                "--limit",
                "5",
            ],
            description="Verify the tailer exits cleanly when nothing new is found.",
        ),
        CommandSpec(
            name="interview",
            args=["aijournal", "interview", "--date", base_day],
            description="Run the fake interview probe generator for the base day.",
        ),
    ]


def _run_command(spec: CommandSpec, cwd: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("AIJOURNAL_FAKE_OLLAMA", "1")
    full_args = ["uv", "run", *spec.args]
    start = time.perf_counter()
    proc = subprocess.run(  # noqa: S603 (trusted repo command)
        full_args,
        check=False,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    duration = time.perf_counter() - start
    succeeded = proc.returncode == 0
    return {
        "name": spec.name,
        "description": spec.description,
        "command": " ".join(shlex.quote(arg) for arg in full_args),
        "expect_success": spec.expect_success,
        "succeeded": succeeded,
        "met_expectation": succeeded == spec.expect_success,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "duration_seconds": duration,
    }


def _persist_results(results: list[dict[str, Any]], repo_root: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target_dir = repo_root / "derived" / "cli_runs"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"cli_smoke_{timestamp}.json"
    payload = {
        "generated_at": timestamp,
        "results": results,
    }
    target_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target_path


def _summarize(results: list[dict[str, Any]]) -> None:
    failures: list[dict[str, Any]] = []
    for entry in results:
        status = "PASS" if entry["succeeded"] else "FAIL"
        marker = ""
        if not entry["met_expectation"]:
            marker = " (unexpected result)"
            failures.append(entry)
        elif not entry["expect_success"]:
            marker = " (expected failure)"

        print(f"[{status}] {entry['name']}{marker} — {entry['duration_seconds']:.2f}s")
        if entry.get("description"):
            print(f"    {entry['description']}")

        if entry.get("stdout"):
            print("    stdout:")
            for line in entry["stdout"].splitlines():
                print(f"      {line}")

        if entry.get("stderr"):
            print("    stderr:")
            for line in entry["stderr"].splitlines():
                print(f"      {line}")

        print()

    if failures:
        print(f"{len(failures)} command(s) diverged from expectation.")
        raise SystemExit(1)

    print("All commands matched expectations.")


def _reset_persona_core(repo_root: Path) -> bool:
    target = repo_root / "derived" / "persona" / "persona_core.yaml"
    if target.exists():
        target.unlink()
        return True
    return False


def _detect_default_day(repo_root: Path) -> str:
    normalized_root = repo_root / "data" / "normalized"
    candidates = sorted(
        [path.name for path in normalized_root.iterdir() if path.is_dir() and path.name],
    )
    if not candidates:
        msg = "No normalized entries found under data/normalized/."
        raise RuntimeError(msg)
    return candidates[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-day",
        default=None,
        help="Date (YYYY-MM-DD) that already has normalized entries. Defaults to latest present.",
    )
    parser.add_argument(
        "--skip-persona-reset",
        action="store_true",
        help="Keep any existing persona_core.yaml instead of forcing a rebuild scenario.",
    )
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    if args.base_day:
        base_day = args.base_day
    else:
        base_day = _detect_default_day(repo_root)

    if not args.skip_persona_reset and _reset_persona_core(repo_root):
        print("Reset persona_core.yaml to simulate fresh build scenario.")

    print(f"Using base day {base_day} for LLM-backed commands.")
    specs = _build_specs(base_day)
    results = [_run_command(spec, repo_root) for spec in specs]
    _persist_results(results, repo_root)
    _summarize(results)


if __name__ == "__main__":
    main()
