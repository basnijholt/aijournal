"""Schema validation for prompt example payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from aijournal.domain.changes import ProfileUpdateProposals
from aijournal.domain.facts import DailySummary, MicroFactsFile
from aijournal.domain.persona import InterviewSet
from aijournal.domain.prompts import (
    PromptMicroFacts,
    PromptProfileUpdates,
    convert_prompt_microfacts,
    convert_prompt_updates_to_proposals,
)
from aijournal.models.derived import AdviceCard

if TYPE_CHECKING:
    from collections.abc import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "prompts" / "examples"


def _load_example(name: str) -> dict[str, Any]:
    path = EXAMPLES_DIR / name
    assert path.exists(), f"Missing example file: {path}"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.parametrize(
    ("filename", "response_model", "target_model", "domain_keys", "converter", "converter_kwargs"),
    [
        ("summarize.json", DailySummary, DailySummary, None, None, {}),
        (
            "extract_facts.json",
            PromptMicroFacts,
            MicroFactsFile,
            None,
            convert_prompt_microfacts,
            {},
        ),
        (
            "profile_update.json",
            PromptProfileUpdates,
            ProfileUpdateProposals,
            None,
            convert_prompt_updates_to_proposals,
            {
                "normalized_ids": ["entry-1"],
                "manifest_hashes": ["manifest-1"],
            },
        ),
        ("advise.json", AdviceCard, AdviceCard, None, None, {}),
    ],
)
def test_prompt_examples_validate_against_models(
    filename: str,
    response_model: type,
    target_model: type | None,
    domain_keys: set[str] | None,
    converter,
    converter_kwargs,
) -> None:
    """Each example must validate against response and domain schemas."""
    payload = _load_example(filename)

    # Response model (LLM output contract).
    instance = response_model.model_validate(payload)
    assert instance is not None

    # Domain model (persisted artifact) when applicable.
    if target_model is not None:
        if converter is not None:
            domain_instance = converter(instance, **converter_kwargs)
            target_model.model_validate(domain_instance.model_dump(mode="python"))
        else:
            domain_payload: dict[str, Any]
            if domain_keys:
                domain_payload = {key: payload[key] for key in domain_keys if key in payload}
            else:
                domain_payload = payload
            target_model.model_validate(domain_payload)


def test_interview_example_matches_schema() -> None:
    payload = _load_example("interview.json")
    interview = InterviewSet.model_validate(payload)
    assert interview.questions, "Interview example should include at least one question"


def test_all_example_files_are_valid_json() -> None:
    for path in sorted(EXAMPLES_DIR.glob("*.json")):
        with path.open(encoding="utf-8") as handle:
            json.load(handle)


def test_expected_examples_present() -> None:
    required: Iterable[str] = {
        "summarize.json",
        "extract_facts.json",
        "profile_update.json",
        "advise.json",
        "interview.json",
    }
    present = {path.name for path in EXAMPLES_DIR.glob("*.json")}
    assert required.issubset(present)
