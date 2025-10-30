"""Helpers for persona calibration ingestion and metrics."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.claims import ClaimAtom
from aijournal.domain.enums import ClaimType
from aijournal.domain.evaluations import (
    CalibrationRecord,
    ClaimAlignment,
    EMAObservation,
    PersonaMetrics,
    SurveyResponse,
    summarise_ema,
    summarise_scale,
)
from aijournal.io.artifacts import load_artifact, save_artifact
from aijournal.utils import time as time_utils


def _load_structured_file(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return yaml.safe_load(text)


def _parse_surveys(payload: Any) -> list[SurveyResponse]:
    if payload is None:
        return []
    if isinstance(payload, dict) and "surveys" in payload:
        payload = payload.get("surveys")
    if not isinstance(payload, list):
        raise ValueError("Survey payload must be a list of responses or contain a `surveys` list")
    responses: list[SurveyResponse] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        data = {
            "date": entry.get("date") or entry.get("timestamp") or "",
            "instrument": entry.get("instrument") or "unknown",
            "respondent": entry.get("respondent"),
            "scales": entry.get("scales") or entry.get("scores") or {},
        }
        responses.append(SurveyResponse.model_validate(data))
    return responses


def _parse_ema(payload: Any) -> list[EMAObservation]:
    if payload is None:
        return []
    if isinstance(payload, dict) and "ema" in payload:
        payload = payload.get("ema")
    if not isinstance(payload, list):
        raise ValueError("EMA payload must be a list of observations or contain an `ema` list")
    observations: list[EMAObservation] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        data = {
            "timestamp": entry.get("timestamp") or entry.get("date") or entry.get("time") or "",
            "label": entry.get("label") or entry.get("scale") or "unknown",
            "value": entry.get("value"),
        }
        observations.append(EMAObservation.model_validate(data))
    return observations


def ingest_calibration(
    *,
    root: Path,
    survey_payload: Any | None,
    ema_payload: Any | None,
) -> Path:
    """Persist a calibration artifact built from survey and EMA payloads."""

    surveys = _parse_surveys(survey_payload)
    ema = _parse_ema(ema_payload)
    if not surveys and not ema:
        raise ValueError("Provide at least one survey or EMA datapoint")

    record = CalibrationRecord(surveys=surveys, ema=ema)
    created_at = time_utils.format_timestamp(time_utils.now())
    notes: dict[str, str] = {
        "survey_count": str(len(surveys)),
        "ema_count": str(len(ema)),
    }
    artifact = Artifact[CalibrationRecord](
        kind=ArtifactKind.PERSONA_CALIBRATION,
        meta=ArtifactMeta(
            created_at=created_at,
            notes=notes,
        ),
        data=record,
    )
    directory = root / "derived" / "persona" / "calibration"
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"calibration_{created_at.replace(':', '').replace('-', '').replace('T', '_').replace('Z', '')}.yaml"
    path = directory / filename
    save_artifact(path, artifact)
    return path


def load_calibration_records(root: Path) -> list[CalibrationRecord]:
    directory = root / "derived" / "persona" / "calibration"
    if not directory.exists():
        return []
    records: list[CalibrationRecord] = []
    for file in sorted(directory.glob("*.yaml")):
        try:
            artifact = load_artifact(file, CalibrationRecord)
        except Exception:  # pragma: no cover - guard corrupt files
            continue
        records.append(artifact.data)
    return records


def _aggregate_surveys(records: Iterable[CalibrationRecord]) -> dict[str, list[float]]:
    values: dict[str, list[float]] = defaultdict(list)
    for record in records:
        for response in record.surveys:
            for scale, value in response.scales.items():
                try:
                    values[scale].append(float(value))
                except (TypeError, ValueError):
                    continue
    return values


def _aggregate_ema(records: Iterable[CalibrationRecord]) -> dict[str, list[float]]:
    values: dict[str, list[float]] = defaultdict(list)
    for record in records:
        for observation in record.ema:
            try:
                values[observation.label].append(float(observation.value))
            except (TypeError, ValueError):
                continue
    return values


def _claim_alignments(
    *,
    claims: Sequence[ClaimAtom],
    survey_summary: dict[str, float],
) -> list[ClaimAlignment]:
    alignments: list[ClaimAlignment] = []
    for claim in claims:
        if claim.type not in {
            ClaimType.TRAIT,
            ClaimType.HABIT,
            ClaimType.PREFERENCE,
            ClaimType.VALUE,
        }:
            continue
        key = claim.subject.lower() if claim.subject else claim.statement.lower()
        matched_scale = None
        mean_score = None
        for scale, value in survey_summary.items():
            if scale.lower() == key:
                matched_scale = scale
                mean_score = value
                break
        delta = None
        if mean_score is not None:
            delta = float(claim.strength) - float(mean_score)
        alignments.append(
            ClaimAlignment(
                claim_id=claim.id,
                statement=claim.statement,
                claim_strength=claim.strength,
                matched_scale=matched_scale,
                scale_mean=mean_score,
                delta=delta,
            ),
        )
    return alignments


def compute_persona_metrics(
    *,
    root: Path,
    records: Sequence[CalibrationRecord],
    claims: Sequence[ClaimAtom],
) -> Path:
    if not records:
        raise ValueError(
            "No calibration records found; run `aijournal ops persona calibrate` first"
        )

    survey_values = _aggregate_surveys(records)
    ema_values = _aggregate_ema(records)
    survey_summary_models = summarise_scale(survey_values)
    ema_summary_models = summarise_ema(ema_values)
    survey_means = {name: summary.mean for name, summary in survey_summary_models.items()}
    alignments = _claim_alignments(claims=claims, survey_summary=survey_means)

    generated_at = datetime.now(tz=UTC).isoformat()
    metrics = PersonaMetrics(
        generated_at=generated_at,
        survey_summary=survey_summary_models,
        ema_summary=ema_summary_models,
        claim_alignment=alignments,
    )

    artifact = Artifact[PersonaMetrics](
        kind=ArtifactKind.PERSONA_METRICS,
        meta=ArtifactMeta(created_at=generated_at),
        data=metrics,
    )

    directory = root / "derived" / "persona" / "metrics"
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"metrics_{generated_at.replace(':', '').replace('-', '').replace('T', '_')}.yaml"
    path = directory / filename
    save_artifact(path, artifact)
    return path
