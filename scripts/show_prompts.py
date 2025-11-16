#!/usr/bin/env python3

"""Extract human-readable prompts/messages from Codex JSONL session logs.

Usage:
    uv run python scripts/show_prompts.py \
        ~/.codex/sessions/.../rollout-....jsonl [--types user_message,agent_message]

By default we show `user_message` and `agent_message` payloads because they
represent the operator instructions ("prompts") exchanged with the CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "log_path",
        type=Path,
        help="Path to the Codex JSONL session file",
    )
    parser.add_argument(
        "--types",
        default="user_message,agent_message",
        help=("Comma-separated payload types to display (default: user_message,agent_message)."),
    )
    parser.add_argument(
        "--no-divider",
        action="store_true",
        help="Disable divider lines between prompts for easier piping.",
    )
    return parser.parse_args()


def iter_prompts(path: Path, allowed_types: set[str]) -> Iterable[tuple[int, str, str, str]]:
    """Yield (line_no, timestamp, payload_type, message) tuples."""
    with path.open("r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:  # pragma: no cover - log noise only
                print(
                    f"Skipping line {line_no}: invalid JSON ({exc})",
                    file=sys.stderr,
                )
                continue

            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue

            payload_type = payload.get("type")
            if allowed_types and payload_type not in allowed_types:
                continue

            message = payload.get("message")
            if not message:
                continue

            timestamp = record.get("timestamp", "?")
            yield line_no, timestamp, payload_type or "?", message


def main() -> None:
    args = parse_args()
    allowed = {entry.strip() for entry in args.types.split(",") if entry.strip()}

    if not args.log_path.exists():
        print(f"error: {args.log_path} does not exist", file=sys.stderr)
        sys.exit(1)

    divider = "" if args.no_divider else "-" * 80

    found = False
    for line_no, timestamp, payload_type, message in iter_prompts(args.log_path, allowed):
        found = True
        header = f"{line_no:>7} | {timestamp} | {payload_type}"
        print(header)
        print(message.rstrip())
        if divider:
            print(divider)

    if not found:
        selected = ", ".join(sorted(allowed)) or "<any>"
        print(
            f"No prompts found for payload types: {selected}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
