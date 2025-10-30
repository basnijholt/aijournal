"""Schema validation for prompt example payloads."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from aijournal.domain.changes import ProfileUpdateProposals
from aijournal.domain.facts import DailySummary, MicroFactsFile
from aijournal.domain.persona import InterviewSet
from aijournal.models.derived import AdviceCard
from aijournal.models.responses import AdviceLLMResponse

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "prompts" / "examples"


def _load_example(name: str) -> dict[str, Any]:
    path = EXAMPLES_DIR / name
    assert path.exists(), f"Missing example file: {path}"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.parametrize(
    "filename,response_model,target_model,domain_keys",
    [
        ("summarize.json", DailySummary, DailySummary, None),
        ("extract_facts.json", MicroFactsFile, MicroFactsFile, None),
        ("characterize.json", ProfileUpdateProposals, ProfileUpdateProposals, None),
        ("profile_suggest.json", ProfileUpdateProposals, ProfileUpdateProposals, None),
        ("advise.json", AdviceLLMResponse, AdviceCard, None),
    ],
)
def test_prompt_examples_validate_against_models(
    filename: str,
    response_model: type,
    target_model: type | None,
    domain_keys: set[str] | None,
) -> None:
    """Each example must validate against response and domain schemas."""

    payload = _load_example(filename)

    # Response model (LLM output contract).
    instance = response_model.model_validate(payload)
    assert instance is not None

    # Domain model (persisted artifact) when applicable.
    if target_model is not None:
        domain_payload: dict[str, Any]
        if domain_keys:
            domain_payload = {key: payload[key] for key in domain_keys if key in payload}
        else:
            domain_payload = payload
        target_model.model_validate(domain_payload)


def test_profile_suggest_example_matches_strict_schema() -> None:
    payload = _load_example("profile_suggest.json")
    proposals = ProfileUpdateProposals.model_validate(payload)
    assert len(proposals.claims) == 1
    assert len(proposals.facets) == 1


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
        "characterize.json",
        "profile_suggest.json",
        "advise.json",
        "interview.json",
    }
    present = {path.name for path in EXAMPLES_DIR.glob("*.json")}
    assert required.issubset(present)
