from __future__ import annotations

import pytest

from aijournal.common.base import StrictModel
from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta, LLMResult


class _Payload(StrictModel):
    value: int


def test_artifact_meta_requires_timestamp() -> None:
    with pytest.raises(Exception):
        ArtifactMeta.model_validate({})


def test_artifact_defaults_and_strictness() -> None:
    meta = ArtifactMeta(created_at="2025-10-29T00:00:00Z")
    artifact = Artifact[_Payload](
        kind=ArtifactKind.SUMMARY_DAILY,
        meta=meta,
        data=_Payload(value=1),
    )
    assert artifact.kind is ArtifactKind.SUMMARY_DAILY
    assert artifact.model_dump().keys() == {"kind", "meta", "data"}

    artifact = Artifact[_Payload](
        kind=ArtifactKind.SUMMARY_DAILY,
        meta=meta,
        data=_Payload(value=1),
        extra_field="nope",  # type: ignore[arg-type]
    )
    assert "extra_field" not in artifact.model_dump()


def test_llm_result_structure() -> None:
    result = LLMResult[_Payload](
        model="gpt-oss:20b",
        prompt_path="prompts/example.md",
        created_at="2025-10-29T00:00:00Z",
        payload=_Payload(value=9),
    )

    assert result.payload.value == 9
