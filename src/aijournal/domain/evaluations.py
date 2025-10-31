"""Domain models for persona evaluation and calibration artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from math import sqrt

from pydantic import Field

from aijournal.common.base import StrictModel


class SurveyResponse(StrictModel):
    """Single survey submission capturing instrument scores."""

    date: str
    instrument: str
    respondent: str | None = None
    scales: dict[str, float] = Field(default_factory=dict)


class EMAObservation(StrictModel):
    """Ecological momentary assessment datapoint."""

    timestamp: str
    label: str
    value: float


class CalibrationRecord(StrictModel):
    """Aggregated payload persisted after a calibration ingestion run."""

    surveys: list[SurveyResponse] = Field(default_factory=list)
    ema: list[EMAObservation] = Field(default_factory=list)


class ScaleSummary(StrictModel):
    """Summary statistics for a survey scale."""

    mean: float
    count: int


class EMASummary(StrictModel):
    """Summary statistics for an EMA label."""

    mean: float
    stddev: float | None
    count: int


class ClaimAlignment(StrictModel):
    """Alignment metrics between persona claims and observed evidence."""

    claim_id: str
    statement: str
    claim_strength: float
    matched_scale: str | None
    scale_mean: float | None
    delta: float | None


class PersonaMetrics(StrictModel):
    """Metrics artifact summarising calibration data and persona alignment."""

    generated_at: str
    survey_summary: dict[str, ScaleSummary] = Field(default_factory=dict)
    ema_summary: dict[str, EMASummary] = Field(default_factory=dict)
    claim_alignment: list[ClaimAlignment] = Field(default_factory=list)


def summarise_scale(values: Mapping[str, list[float]]) -> dict[str, ScaleSummary]:
    """Compute means for each survey scale given value lists."""

    summary: dict[str, ScaleSummary] = {}
    for name, items in values.items():
        if not items:
            continue
        mean = sum(items) / len(items)
        summary[name] = ScaleSummary(mean=mean, count=len(items))
    return summary


def summarise_ema(values: Mapping[str, list[float]]) -> dict[str, EMASummary]:
    """Compute mean/stddev for EMA labels given value lists."""

    summary: dict[str, EMASummary] = {}
    for name, items in values.items():
        n = len(items)
        if n == 0:
            continue
        mean = sum(items) / n
        stddev = None
        if n > 1:
            mean_square = sum(value * value for value in items) / n
            variance = max(mean_square - mean * mean, 0.0)
            stddev = sqrt(variance)
        summary[name] = EMASummary(mean=mean, stddev=stddev, count=n)
    return summary


__all__ = [
    "CalibrationRecord",
    "ClaimAlignment",
    "EMAObservation",
    "EMASummary",
    "PersonaMetrics",
    "ScaleSummary",
    "SurveyResponse",
    "summarise_ema",
    "summarise_scale",
]
