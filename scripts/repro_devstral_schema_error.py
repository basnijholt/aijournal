#!/usr/bin/env python
"""Minimal Pydantic AI reproduction for structured-output schema failures."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from string import Template
from typing import Any

import yaml
from pydantic import BaseModel

from aijournal.domain.changes import ProfileUpdateProposals
from aijournal.services.ollama import build_ollama_agent, build_ollama_config_from_mapping

STRUCTURED_SYSTEM_PROMPT = (
    "You are part of the local aijournal CLI. "
    "Read the user's prompt carefully and respond with JSON that matches the declared response schema. "
    "Do not include markdown fences or commentary."
)


class MiniProfileProposals(BaseModel):
    summary: str
    ideas: list[str]


def dump_exc(exc: BaseException) -> None:
    print("\nStructured call failed:", file=sys.stderr)
    print(f"  type: {type(exc)!r}", file=sys.stderr)
    print(f"  args: {exc.args!r}", file=sys.stderr)
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if cause is not None:
        print("\nCaused by:", file=sys.stderr)
        print(f"  type: {type(cause)!r}", file=sys.stderr)
        print(f"  args: {cause.args!r}", file=sys.stderr)
    print("\nTraceback:", file=sys.stderr)
    traceback.print_exc()


def _json_block(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def load_profile_inputs(date: str) -> dict[str, str]:
    base = Path.cwd()
    entry_dir = base / "data" / "normalized" / date
    entries: list[dict[str, Any]] = []
    for path in sorted(entry_dir.glob("*.yaml")):
        entries.append(yaml.safe_load(path.read_text(encoding="utf-8")))
    profile_path = base / "profile" / "self_profile.yaml"
    claims_path = base / "profile" / "claims.yaml"
    profile_data = (
        yaml.safe_load(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else {}
    )
    claims_data = (
        yaml.safe_load(claims_path.read_text(encoding="utf-8")).get("claims", [])
        if claims_path.exists()
        else []
    )
    return {
        "entries_json": _json_block(entries),
        "profile_json": _json_block(profile_data),
        "claims_json": _json_block({"claims": claims_data}),
    }


def build_prompt(mode: str, date: str) -> tuple[type[BaseModel], str]:
    if mode == "mini":
        prompt = (
            "Summarize why schema-constrained responses might fail and list three debugging ideas."
        )
        return MiniProfileProposals, prompt
    if mode == "simple":
        prompt = """
Return JSON exactly following the `ProfileUpdateProposals` schema:
{
  "claims": [
    {
      "claim": {
        "type": "goal" | "habit" | "value" | ...,
        "subject": "who or what the claim refers to",
        "predicate": "relationship or attribute",
        "value": "normalized value",
        "statement": "Readable sentence",
        "scope": {"domain": "optional", "context": ["tags"], "conditions": []},
        "strength": 0.0-1.0,
        "status": "accepted" | "tentative" | "rejected",
        "method": "self_report" | "inferred" | "behavioral",
        "user_verified": false,
        "review_after_days": integer
      },
      "normalized_ids": ["normalized-entry-id"],
      "evidence": [
        {"entry_id": "normalized-entry-id", "spans": [{"type": "paragraph", "index": 0}]}
      ],
      "manifest_hashes": ["optional-manifest-hash"],
      "rationale": "≤25 word justification"
    }
  ],
  "facets": [
    {
      "path": "values_motivations.recurring_theme",
      "operation": "set" | "merge" | "remove",
      "value": <JSON value when operation is set/merge>,
      "method": "inferred" | "self_report" | "behavioral",
      "confidence": 0.0-1.0,
      "review_after_days": integer,
      "user_verified": false,
      "evidence": [
        {"entry_id": "normalized-entry-id", "spans": [{"type": "paragraph", "index": 1}]}
      ],
      "rationale": "≤25 word justification"
    }
  ]
}
Use the notes below to ground your output and leave the arrays empty if nothing is justified.

Notes:
- 2024-12-04 journal: Evening walks helped you decompress after work.
- 2024-12-05 journal: Weekly check-ins improved focus and accountability.
"""
        return ProfileUpdateProposals, prompt

    variables = load_profile_inputs(date)
    variables["date"] = date
    template_path = Path("prompts") / "profile_update.md"
    template = Template(template_path.read_text(encoding="utf-8"))
    prompt_text = template.safe_substitute(variables)
    return ProfileUpdateProposals, prompt_text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default=None,
        help="Ollama host URL; overrides AIJOURNAL_OLLAMA_HOST.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Ollama model id; overrides AIJOURNAL_MODEL.",
    )
    parser.add_argument(
        "--mode",
        choices=("mini", "simple", "profile"),
        default="mini",
        help="Select which schema/prompt to exercise.",
    )
    parser.add_argument(
        "--date",
        default="2024-12-04",
        help="Date used when mode=profile (must exist under data/normalized).",
    )
    args = parser.parse_args()

    host = args.host or os.getenv("AIJOURNAL_OLLAMA_HOST")
    model = args.model or os.getenv("AIJOURNAL_MODEL")
    if not host or not model:
        missing = []
        if not host:
            missing.append("--host or AIJOURNAL_OLLAMA_HOST")
        if not model:
            missing.append("--model or AIJOURNAL_MODEL")
        print(f"Missing configuration: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

    print(f"Using Ollama host={host!r}, model={model!r}, mode={args.mode!r}")

    response_model, prompt = build_prompt(args.mode, args.date)

    ollama_config = build_ollama_config_from_mapping(model=model, host=host)
    agent = build_ollama_agent(
        ollama_config,
        system_prompt=STRUCTURED_SYSTEM_PROMPT,
        output_type=response_model,
        name="json-repro",
    )

    try:
        result = agent.run_sync(prompt)
    except Exception as exc:  # noqa: BLE001 - we want full diagnostics
        dump_exc(exc)
        sys.exit(1)

    print("\nCall succeeded. Raw RunOutput:")
    print(result)

    structured = result.output
    if isinstance(structured, response_model):
        print("\nStructured payload:")
        print(structured.model_dump_json(indent=2))
    else:
        print("\nStructured payload not deserialized as expected; dumping repr:")
        print(repr(structured))


if __name__ == "__main__":
    main()
