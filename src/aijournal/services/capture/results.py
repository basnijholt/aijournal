"""Lightweight result models shared by orchestration code."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Iterable


class OperationResult(BaseModel):
    """Outcome of a single operation/stage."""

    ok: bool = True
    changed: bool = False
    message: str = ""
    artifacts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def noop(cls, message: str = "nothing to do", **kwargs: Any) -> OperationResult:
        return cls(ok=True, changed=False, message=message, **kwargs)

    @classmethod
    def wrote(
        cls,
        artifacts: Iterable[str],
        message: str = "written",
        **kwargs: Any,
    ) -> OperationResult:
        artifacts_list = list(artifacts)
        return cls(
            ok=True,
            changed=bool(artifacts_list),
            message=message,
            artifacts=artifacts_list,
            **kwargs,
        )

    @classmethod
    def fail(cls, message: str, **kwargs: Any) -> OperationResult:
        return cls(ok=False, changed=False, message=message, **kwargs)


class StageResult(BaseModel):
    """Execution metadata for a single capture stage."""

    stage: str
    result: OperationResult
    duration_ms: float

    model_config = {"arbitrary_types_allowed": True}
