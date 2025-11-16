from __future__ import annotations

from aijournal.domain.prompts import (
    PromptMicroFact,
    PromptMicroFacts,
    convert_prompt_microfacts,
    is_metadata_only_fact,
)


def _fact(
    *,
    fact_id: str = "fact-1",
    statement: str = "Completed deep work block",
    evidence_entry: str | None = "entry-1",
) -> PromptMicroFact:
    return PromptMicroFact(
        id=fact_id,
        statement=statement,
        confidence=0.9,
        evidence_entry=evidence_entry,
        evidence_para=0,
        first_seen="2025-11-14",
        last_seen="2025-11-14",
    )


def test_is_metadata_only_fact_detects_common_patterns() -> None:
    assert is_metadata_only_fact(_fact(fact_id="entry-created-foo"))
    assert is_metadata_only_fact(_fact(statement="Entry created on 2025-11-14"))
    assert is_metadata_only_fact(_fact(statement="Title is Focus Sprint"))
    assert is_metadata_only_fact(_fact(evidence_entry=None, statement="Any content"))


def test_is_metadata_only_fact_allows_grounded_content() -> None:
    assert not is_metadata_only_fact(
        _fact(statement="Completed 2h focus block on auth plan", fact_id="focus-block"),
    )


def test_convert_prompt_microfacts_filters_metadata_only_entries() -> None:
    prompt = PromptMicroFacts(
        facts=[
            _fact(fact_id="entry-created-foo", statement="Entry created on 2025-11-14"),
            _fact(fact_id="focus-block", statement="Completed 2h focus block"),
        ],
    )

    result = convert_prompt_microfacts(prompt)

    statements = [fact.statement for fact in result.facts]
    assert statements == ["Completed 2h focus block"]
