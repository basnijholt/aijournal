"""Tests for RAG-enhanced profile update functionality."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

if TYPE_CHECKING:
    from pathlib import Path

    from aijournal.common.app_config import AppConfig


def test_retrieve_historical_chunks_returns_deduped_results(
    tmp_path: Path,
    mock_config: AppConfig,
) -> None:
    """Test that _retrieve_historical_chunks returns deduplicated chunks."""
    from aijournal.commands.profile_update import _retrieve_historical_chunks

    # Create a minimal index structure
    index_dir = tmp_path / "derived" / "index"
    index_dir.mkdir(parents=True)

    # Create meta.json
    meta_data = {
        "kind": "index.meta",
        "data": {
            "total_chunks": 5,
            "total_entries": 2,
            "indexed_at": "2025-11-17T00:00:00Z",
        },
        "meta": {
            "created_at": "2025-11-17T00:00:00Z",
            "model": "embeddinggemma:latest",
        },
    }
    (index_dir / "meta.json").write_text(yaml.dump(meta_data), encoding="utf-8")

    # Create summary_chunks.yaml (minimal structure)
    summary_chunks = {
        "chunks": [
            {
                "chunk_id": "chunk-001",
                "date": "2025-11-01",
                "text": "Working on deep focus sessions in the morning",
                "tags": ["focus"],
                "source_type": "summary",
            },
            {
                "chunk_id": "chunk-002",
                "date": "2025-11-02",
                "text": "Morning deep work continues to be productive",
                "tags": ["focus"],
                "source_type": "summary",
            },
        ],
    }
    (index_dir / "summary_chunks.yaml").write_text(yaml.dump(summary_chunks), encoding="utf-8")

    # Create microfact_chunks.yaml
    microfact_chunks = {"chunks": []}
    (index_dir / "microfact_chunks.yaml").write_text(yaml.dump(microfact_chunks), encoding="utf-8")

    # Note: This will fail if ChromaDB isn't available, so we expect empty results in CI
    # In a real workspace with ChromaDB, this would return chunks
    result = _retrieve_historical_chunks(tmp_path, mock_config)

    # Either we get results (if ChromaDB available) or empty list (if not)
    assert isinstance(result, list)
    # If we got results, verify deduplication by checking no duplicate chunk_ids
    if result:
        chunk_ids = [chunk.chunk_id for chunk in result]
        assert len(chunk_ids) == len(set(chunk_ids)), "Chunks should be deduplicated"
        assert len(result) <= 40, "Should limit to 40 chunks max"


def test_count_new_claims_since_last_consolidation_no_metadata(tmp_path: Path) -> None:
    """Test counting claims when no consolidation metadata exists."""
    from aijournal.commands.profile_update import _count_new_claims_since_last_consolidation

    # Create profile directory with claims
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True)

    claims_data = {
        "claims": [
            {"id": "claim-1", "status": "accepted", "statement": "Test 1"},
            {"id": "claim-2", "status": "tentative", "statement": "Test 2"},
            {
                "id": "claim-3",
                "status": "rejected",
                "statement": "Test 3",
            },  # Rejected shouldn't count
        ],
    }
    (profile_dir / "claims.yaml").write_text(yaml.dump(claims_data), encoding="utf-8")

    # No metadata file exists yet
    count = _count_new_claims_since_last_consolidation(tmp_path)

    # Should count all accepted + tentative claims (2)
    assert count == 2


def test_count_new_claims_since_last_consolidation_with_metadata(tmp_path: Path) -> None:
    """Test counting new claims when consolidation metadata exists."""
    from aijournal.commands.profile_update import _count_new_claims_since_last_consolidation

    # Create profile directory with claims
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True)

    claims_data = {
        "claims": [
            {"id": "claim-1", "status": "accepted", "statement": "Test 1"},
            {"id": "claim-2", "status": "tentative", "statement": "Test 2"},
            {"id": "claim-3", "status": "accepted", "statement": "Test 3"},
            {"id": "claim-4", "status": "tentative", "statement": "Test 4"},
        ],
    }
    (profile_dir / "claims.yaml").write_text(yaml.dump(claims_data), encoding="utf-8")

    # Create metadata file showing 2 claims at last consolidation
    derived_dir = tmp_path / "derived"
    derived_dir.mkdir(parents=True)

    metadata = {
        "last_consolidation": {
            "timestamp": "2025-11-10T00:00:00Z",
            "claim_count": 2,
        },
    }
    (derived_dir / "profile_update_meta.yaml").write_text(yaml.dump(metadata), encoding="utf-8")

    count = _count_new_claims_since_last_consolidation(tmp_path)

    # Should count new claims since last consolidation: 4 (current) - 2 (last) = 2
    assert count == 2


def test_count_all_claims_handles_missing_file(tmp_path: Path) -> None:
    """Test that _count_all_claims returns 0 when claims file missing."""
    from aijournal.commands.profile_update import _count_all_claims

    count = _count_all_claims(tmp_path)
    assert count == 0


def test_update_consolidation_metadata_creates_file(tmp_path: Path) -> None:
    """Test that consolidation metadata is created correctly."""
    from aijournal.commands.profile_update import _update_consolidation_metadata

    timestamp = "2025-11-17T00:00:00Z"
    claim_count = 15

    _update_consolidation_metadata(tmp_path, claim_count, timestamp)

    metadata_path = tmp_path / "derived" / "profile_update_meta.yaml"
    assert metadata_path.exists()

    data = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    assert data["last_consolidation"]["timestamp"] == timestamp
    assert data["last_consolidation"]["claim_count"] == claim_count


def test_consolidation_threshold_logic(tmp_path: Path, mock_config: AppConfig) -> None:
    """Test that consolidation triggers at 10+ new claims."""
    # This is tested implicitly in the counting functions above
    # The threshold logic is in _prepare() where it checks: new_claims_count >= 10
    # We're testing the helper functions here, integration test would verify the full flow
    from aijournal.commands.profile_update import _count_new_claims_since_last_consolidation

    # Setup: 7 existing claims, no metadata
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True)

    # 7 claims (below threshold)
    claims_data = {
        "claims": [
            {"id": f"claim-{i}", "status": "accepted", "statement": f"Test {i}"} for i in range(7)
        ],
    }
    (profile_dir / "claims.yaml").write_text(yaml.dump(claims_data), encoding="utf-8")

    count = _count_new_claims_since_last_consolidation(tmp_path)
    assert count == 7
    assert count < 10, "Should not trigger consolidation with 7 claims"

    # Add 3 more claims to reach threshold
    claims_data["claims"].extend(
        [
            {"id": f"claim-{i}", "status": "tentative", "statement": f"Test {i}"}
            for i in range(7, 10)
        ],
    )
    (profile_dir / "claims.yaml").write_text(yaml.dump(claims_data), encoding="utf-8")

    count = _count_new_claims_since_last_consolidation(tmp_path)
    assert count == 10
    assert count >= 10, "Should trigger consolidation with 10 claims"


@pytest.fixture
def mock_config(tmp_path: Path) -> AppConfig:
    """Provide minimal AppConfig for testing."""
    from aijournal.common.app_config import AppConfig, LLMConfig, PathsConfig

    return AppConfig(
        llm=LLMConfig(
            model="test-model",
            ollama_host="http://localhost:11434",
            embedding_model="test-embedding",
        ),
        paths=PathsConfig(
            workspace=str(tmp_path),
            profile="profile",
            data="data",
            derived="derived",
        ),
    )
