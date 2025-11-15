"""Tests for prompt metadata propagation in artifacts and LLM results."""

from __future__ import annotations

from aijournal.common.meta import ArtifactMeta, LLMResult


class TestArtifactMetaWithPromptFields:
    """Tests for ArtifactMeta with prompt_kind and prompt_set."""

    def test_can_create_with_prompt_metadata(self) -> None:
        """Should create ArtifactMeta with prompt_kind and prompt_set."""
        meta = ArtifactMeta(
            created_at="2025-01-15T10:00:00Z",
            model="gpt-oss:20b",
            prompt_path="prompts/summarize_day.md",
            prompt_hash="abc123",
            prompt_kind="summarize_day.md",
            prompt_set="variant-a",
        )
        assert meta.prompt_kind == "summarize_day.md"
        assert meta.prompt_set == "variant-a"

    def test_prompt_fields_are_optional(self) -> None:
        """prompt_kind and prompt_set should be optional."""
        meta = ArtifactMeta(
            created_at="2025-01-15T10:00:00Z",
            model="gpt-oss:20b",
        )
        assert meta.prompt_kind is None
        assert meta.prompt_set is None

    def test_serialization_includes_prompt_fields(self) -> None:
        """Serialized output should include prompt fields."""
        meta = ArtifactMeta(
            created_at="2025-01-15T10:00:00Z",
            prompt_kind="extract_facts.md",
            prompt_set="experiment-1",
        )
        data = meta.model_dump(mode="python")
        assert data["prompt_kind"] == "extract_facts.md"
        assert data["prompt_set"] == "experiment-1"

    def test_deserialization_handles_prompt_fields(self) -> None:
        """Should deserialize prompt fields from dict."""
        data = {
            "created_at": "2025-01-15T10:00:00Z",
            "prompt_kind": "profile_update.md",
            "prompt_set": "test-variant",
        }
        meta = ArtifactMeta.model_validate(data)
        assert meta.prompt_kind == "profile_update.md"
        assert meta.prompt_set == "test-variant"


class TestLLMResultWithPromptFields:
    """Tests for LLMResult with prompt_kind and prompt_set."""

    def test_can_create_with_prompt_metadata(self) -> None:
        """Should create LLMResult with prompt_kind and prompt_set."""
        result = LLMResult[dict](
            model="gpt-oss:20b",
            prompt_path="prompts/advise.md",
            prompt_hash="def456",
            prompt_kind="advise.md",
            prompt_set="variant-b",
            created_at="2025-01-15T10:00:00Z",
            payload={"recommendations": []},
        )
        assert result.prompt_kind == "advise.md"
        assert result.prompt_set == "variant-b"

    def test_prompt_fields_are_optional(self) -> None:
        """prompt_kind and prompt_set should be optional."""
        result = LLMResult[dict](
            model="gpt-oss:20b",
            prompt_path="prompts/summarize.md",
            created_at="2025-01-15T10:00:00Z",
            payload={"summary": "test"},
        )
        assert result.prompt_kind is None
        assert result.prompt_set is None

    def test_serialization_includes_prompt_fields(self) -> None:
        """Serialized output should include prompt fields."""
        result = LLMResult[dict](
            model="gpt-oss:20b",
            prompt_path="prompts/interview.md",
            prompt_kind="interview.md",
            prompt_set="control",
            created_at="2025-01-15T10:00:00Z",
            payload={"questions": []},
        )
        data = result.model_dump(mode="python")
        assert data["prompt_kind"] == "interview.md"
        assert data["prompt_set"] == "control"

    def test_deserialization_handles_prompt_fields(self) -> None:
        """Should deserialize prompt fields from dict."""
        data = {
            "model": "gpt-oss:20b",
            "prompt_path": "prompts/extract_facts.md",
            "prompt_kind": "extract_facts.md",
            "prompt_set": "experiment-2",
            "created_at": "2025-01-15T10:00:00Z",
            "payload": {"facts": []},
        }
        result = LLMResult[dict].model_validate(data)
        assert result.prompt_kind == "extract_facts.md"
        assert result.prompt_set == "experiment-2"

    def test_backward_compatibility_without_prompt_fields(self) -> None:
        """Should handle old payloads without prompt_kind/prompt_set."""
        data = {
            "model": "gpt-oss:20b",
            "prompt_path": "prompts/summarize.md",
            "created_at": "2025-01-15T10:00:00Z",
            "payload": {"day": "2025-01-15"},
        }
        result = LLMResult[dict].model_validate(data)
        assert result.prompt_kind is None
        assert result.prompt_set is None
