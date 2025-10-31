"""Unit tests for typed claim atom models."""

from __future__ import annotations

import pytest

from aijournal.domain.claims import ClaimAtom, ClaimAtomsFile


def _sample_atom_dict() -> dict:
    return {
        "id": "pref.deep_work.window",
        "type": "preference",
        "subject": "deep_work",
        "predicate": "best_window",
        "value": "09:00-12:00",
        "statement": "Best deep work between 09:00–12:00 on weekdays.",
        "scope": {
            "domain": "work",
            "context": ["weekday"],
            "conditions": [],
        },
        "strength": 0.78,
        "status": "accepted",
        "method": "inferred",
        "user_verified": True,
        "review_after_days": 120,
        "provenance": {
            "sources": [
                {
                    "entry_id": "2025-10-25_x9t3",
                    "spans": [{"type": "para", "index": 0}],
                },
            ],
            "first_seen": "2024-11-02",
            "last_updated": "2025-10-25T10:10:00Z",
        },
    }


def test_claim_atom_model_round_trip() -> None:
    atom = ClaimAtom.model_validate(_sample_atom_dict())
    assert atom.type == "preference"
    assert atom.status == "accepted"
    assert atom.method == "inferred"
    assert atom.scope.domain == "work"
    assert atom.provenance.sources[0].spans[0].index == 0

    dumped = atom.model_dump()
    assert dumped["scope"]["context"] == ["weekday"]
    assert dumped["provenance"]["sources"][0]["entry_id"] == "2025-10-25_x9t3"


def test_claim_atoms_file_container() -> None:
    atoms_file = ClaimAtomsFile.model_validate({"claims": [_sample_atom_dict()]})
    assert len(atoms_file.claims) == 1
    assert atoms_file.claims[0].value == "09:00-12:00"


def test_claim_atom_rejects_provenance_with_text() -> None:
    payload = _sample_atom_dict()
    payload["provenance"]["sources"][0]["spans"][0]["text"] = "sensitive"

    with pytest.raises(ValueError, match="must not carry raw text"):
        ClaimAtom.model_validate(payload)
