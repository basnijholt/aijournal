"""Utilities for applying chat feedback to claim strengths."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from aijournal.common.meta import Artifact, ArtifactKind, ArtifactMeta
from aijournal.domain.enums import FeedbackDirection
from aijournal.domain.events import FeedbackAdjustmentEvent, FeedbackBatch
from aijournal.io.artifacts import save_artifact
from aijournal.io.yaml_io import load_yaml_model, write_yaml_model
from aijournal.models.authoritative import ClaimsFile

if TYPE_CHECKING:
    from pathlib import Path

_CLAIM_PATTERN: Final[re.Pattern[str]] = re.compile(r"\[claim:([A-Za-z0-9_.:-]+)\]")


@dataclass(frozen=True)
class FeedbackAdjustment:
    """Result of nudging a claim strength via thumbs up/down."""

    claim_id: str
    old_strength: float
    new_strength: float
    delta: float


def extract_claim_markers(answer: str) -> list[str]:
    """Return claim IDs referenced in `[claim:<id>]` markers."""
    return [match for match in _CLAIM_PATTERN.findall(answer or "") if match]


def _feedback_delta(feedback: str) -> float:
    text = feedback.lower().strip()
    if text == "up":
        return 0.03
    if text == "down":
        return -0.05
    msg = f"Unsupported feedback value: {feedback}"
    raise ValueError(msg)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _sanitize_session_id(session_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", session_id).strip("-")
    return cleaned or "chat"


def _pending_updates_dir(root: Path) -> Path:
    return root / "derived" / "pending" / "profile_updates"


def apply_chat_feedback(
    root: Path,
    *,
    turn_answer: str,
    question: str,
    session_id: str,
    timestamp: str,
    feedback: str,
) -> tuple[list[FeedbackAdjustment], Path | None]:
    """Adjust claim strengths based on feedback and queue a review file."""
    claim_ids = extract_claim_markers(turn_answer)
    if not claim_ids:
        return ([], None)

    claims_path = root / "profile" / "claims.yaml"
    if not claims_path.exists():
        return ([], None)

    claims_file = load_yaml_model(claims_path, ClaimsFile)
    delta = _feedback_delta(feedback)
    adjustments: list[FeedbackAdjustment] = []

    # Apply nudges to matching claims
    for claim in claims_file.claims:
        if claim.id not in claim_ids:
            continue
        old_strength = float(claim.strength)
        new_strength = _clamp(old_strength + delta)
        if new_strength == old_strength:
            continue
        claim.strength = new_strength
        adjustments.append(
            FeedbackAdjustment(
                claim_id=claim.id,
                old_strength=old_strength,
                new_strength=new_strength,
                delta=new_strength - old_strength,
            ),
        )

    if not adjustments:
        return ([], None)

    write_yaml_model(claims_path, ClaimsFile(claims=claims_file.claims))

    pending_dir = _pending_updates_dir(root)
    pending_dir.mkdir(parents=True, exist_ok=True)
    slug = _sanitize_session_id(session_id)
    timestamp_slug = timestamp.replace(":", "").replace("T", "-")
    feedback_path = pending_dir / f"feedback_{slug}_{timestamp_slug}.yaml"

    feedback_value = FeedbackDirection.DOWN if delta < 0 else FeedbackDirection.UP

    batch = FeedbackBatch(
        batch_id=f"{slug}-{timestamp_slug}",
        created_at=timestamp,
        session_id=session_id,
        question=question,
        feedback=feedback_value,
        events=[
            FeedbackAdjustmentEvent(
                claim_id=adj.claim_id,
                old_strength=adj.old_strength,
                new_strength=adj.new_strength,
                delta=adj.delta,
            )
            for adj in adjustments
        ],
    )
    save_artifact(
        feedback_path,
        Artifact[FeedbackBatch](
            kind=ArtifactKind.FEEDBACK_BATCH,
            meta=ArtifactMeta(created_at=timestamp, model=None),
            data=batch,
        ),
    )
    return adjustments, feedback_path
