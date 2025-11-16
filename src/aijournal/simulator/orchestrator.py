"""Orchestrator that drives the capture pipeline end-to-end for simulation."""

from __future__ import annotations

import os
import shutil
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from aijournal.api.capture import CaptureInput, CaptureRequest
from aijournal.common.constants import DEFAULT_LLM_RETRIES
from aijournal.services.capture import CaptureResult, run_capture
from aijournal.utils import time as time_utils

from .fixtures import FixtureWorkspace, build_fixture_workspace
from .validators import (
    StageValidatorRegistry,
    ValidationReport,
    ValidatorContext,
    render_failures_compact,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(slots=True)
class SimulationReport:
    """Final report returned by the simulator run."""

    workspace: Path
    fixtures: FixtureWorkspace
    capture_result: CaptureResult
    validation: ValidationReport
    kept_workspace: bool

    def summary_lines(self) -> list[str]:
        lines = [
            f"Workspace: {self.workspace}",
            f"Inputs: {len(self.fixtures.entries)} files",
            f"Stages completed: {self.capture_result.stages_completed}",
            f"Validation: {'ok' if self.validation.ok else 'failed'}",
            "Workspace retained" if self.kept_workspace else "Workspace cleaned up",
        ]
        if self.validation.failures:
            lines.append("\n" + render_failures_compact(self.validation.failures))
        return lines

    def render(self) -> str:
        return "\n".join(self.summary_lines())


class HumanSimulator:
    """Generate fixtures, run capture, and validate early-stage invariants."""

    def __init__(
        self,
        *,
        max_stage: int = 8,
        clock: datetime | None = None,
        validators: StageValidatorRegistry | None = None,
        pack_level: Literal["L1", "L3", "L4"] = "L1",
    ) -> None:
        if max_stage < 0:
            msg = "max_stage must be >= 0"
            raise ValueError(msg)
        if max_stage > 8:
            msg = "max_stage must be <= 8"
            raise ValueError(msg)
        normalized_pack = cast(Literal["L1", "L3", "L4"], pack_level)
        self.max_stage = max_stage
        self.clock = clock or datetime(2025, 1, 5, 9, 0, tzinfo=UTC)
        self.validators = validators or StageValidatorRegistry()
        self.pack_level = normalized_pack

    def run(
        self,
        *,
        workspace: Path | None = None,
        keep_workspace: bool = False,
    ) -> SimulationReport:
        fixtures = build_fixture_workspace(workspace)
        keep_flag = keep_workspace or workspace is not None
        capture_result = self._run_capture(fixtures)
        ctx = ValidatorContext(workspace=fixtures.root, capture=capture_result)
        validation = self.validators.run(
            ctx,
            stages=list(range(self.max_stage + 1)),
        )
        report = SimulationReport(
            workspace=fixtures.root,
            fixtures=fixtures,
            capture_result=capture_result,
            validation=validation,
            kept_workspace=keep_flag,
        )
        if not keep_flag:
            shutil.rmtree(fixtures.root, ignore_errors=True)
        return report

    def _run_capture(self, fixtures: FixtureWorkspace) -> CaptureResult:
        request = CaptureRequest(
            source="dir",
            paths=[str(fixtures.input_dir)],
            source_type="journal",
            snapshot=False,
            progress=False,
            apply_profile="auto",
            rebuild="auto",
            pack=self.pack_level,
            retries=DEFAULT_LLM_RETRIES,
        )
        capture_input = CaptureInput.from_request(
            request,
            min_stage=0,
            max_stage=self.max_stage,
        )

        with ExitStack() as stack:
            stack.enter_context(_frozen_time(self.clock))
            stack.enter_context(_patched_env(AIJOURNAL_FAKE_OLLAMA="1"))
            return run_capture(capture_input, root=fixtures.root)


@contextmanager
def _frozen_time(moment: datetime):
    original = time_utils.now
    time_utils.now = lambda: moment
    try:
        yield
    finally:
        time_utils.now = original


@contextmanager
def _patched_env(**updates: str):
    original: dict[str, str | None] = {key: os.environ.get(key) for key in updates}
    os.environ.update({key: value for key, value in updates.items() if value is not None})
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
