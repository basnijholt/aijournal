"""Tests for the human-style simulator harness."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

from aijournal.simulator.orchestrator import HumanSimulator
from aijournal.simulator.validators import StageValidatorRegistry, ValidatorContext

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def simulator_workspace(tmp_path: Path) -> Path:
    return tmp_path / "sim-workspace"


def test_simulator_runs_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    simulator_workspace: Path,
) -> None:
    monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")
    simulator = HumanSimulator(max_stage=7)
    report = simulator.run(workspace=simulator_workspace, keep_workspace=True)

    assert report.validation.ok

    expected_stage_map = {
        0: {"persist"},
        1: {"normalize"},
        2: {"derive.summarize"},
        3: {"derive.extract_facts"},
        4: {"derive.profile_update", "derive.review"},
        5: {"refresh.index"},
        6: {"refresh.persona"},
        7: {"derive.pack"},
    }
    seen_stage_ids: set[int] = set()
    for stage_result in report.capture_result.stage_results:
        for stage_id, names in expected_stage_map.items():
            if stage_result.stage in names:
                seen_stage_ids.add(stage_id)
                break

    assert seen_stage_ids == set(range(8)), "missing stage results for one or more stages"
    assert report.workspace.exists()

    changed_dates = {
        entry.date for entry in report.capture_result.entries if entry.changed and not entry.deduped
    }
    assert changed_dates  # sanity guard

    # Tamper with one artifact to ensure validators catch regressions.
    first_date = sorted(changed_dates)[0]
    microfacts_path = report.workspace / "derived" / "microfacts" / f"{first_date}.yaml"
    if microfacts_path.exists():
        microfacts_path.unlink()
    tampered = StageValidatorRegistry().run(
        ValidatorContext(workspace=report.workspace, capture=report.capture_result),
        stages=[3],
    )
    assert not tampered.ok

    shutil.rmtree(report.workspace, ignore_errors=True)
