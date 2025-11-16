from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from aijournal.pipelines import pack
from aijournal.pipelines.pack import PackAssemblyError
from aijournal.utils import time as time_utils

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, content: str = "sample") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_collect_pack_entries_l2(tmp_path: Path) -> None:
    root = tmp_path
    _write(root / "derived" / "persona" / "persona_core.yaml")
    _write(root / "profile" / "self_profile.yaml")
    _write(root / "profile" / "claims.yaml")
    day = "2024-01-02"
    _write(root / "derived" / "summaries" / f"{day}.yaml")
    _write(root / "derived" / "microfacts" / f"{day}.yaml")
    _write(root / "data" / "normalized" / day / "entry.yaml")

    entries = pack.collect_pack_entries(root, "L2", day, history_days=0)
    roles = [role for role, _ in entries]
    assert roles.count("persona_core") == 1
    assert "summaries" in roles
    assert "microfacts" in roles


def test_collect_pack_entries_l3_includes_consolidated(tmp_path: Path) -> None:
    root = tmp_path
    _write(root / "derived" / "persona" / "persona_core.yaml")
    _write(root / "profile" / "self_profile.yaml")
    _write(root / "profile" / "claims.yaml")
    day = "2024-01-02"
    _write(root / "derived" / "summaries" / f"{day}.yaml")
    _write(root / "derived" / "microfacts" / f"{day}.yaml")
    _write(root / "derived" / "microfacts" / "consolidated.yaml")
    _write(root / "data" / "normalized" / day / "entry.yaml")

    entries = pack.collect_pack_entries(root, "L3", day, history_days=0)
    roles = [role for role, _ in entries]
    assert "consolidated_microfacts" in roles


def test_collect_pack_entries_missing_required(tmp_path: Path) -> None:
    root = tmp_path
    with pytest.raises(PackAssemblyError):
        pack.collect_pack_entries(root, "L1", "2024-01-02", history_days=0)


def test_trim_entries_respects_priority() -> None:
    entries = [
        pack.PackEntry(role="persona_core", path="persona.yaml", tokens=100, content=""),
        pack.PackEntry(role="advice", path="advice.yaml", tokens=50, content=""),
        pack.PackEntry(role="summaries", path="sum.yaml", tokens=40, content=""),
    ]
    trimmed: list[pack.TrimmedFile] = []
    pack.trim_entries(entries, budget=80, trimmed=trimmed)

    assert entries[0].tokens == 100  # core stays intact
    assert entries[1].tokens == 0
    assert trimmed[0].role == "advice"


def test_build_pack_payload_includes_metadata() -> None:
    entries = [pack.PackEntry(role="persona_core", path="persona.yaml", tokens=10, content="data")]
    trimmed: list[pack.TrimmedFile] = []
    bundle = pack.build_pack_payload(entries, "L1", "2024-01-02", trimmed, 10, 100)
    assert bundle.meta.total_tokens == 10
    assert bundle.meta.generated_at <= time_utils.format_timestamp(time_utils.now())
    assert bundle.level == "L1"
