"""Domain models describing claim change events and feedback adjustments."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from aijournal.common.base import StrictModel
from aijournal.domain.claims import ClaimSource  # noqa: TC001
from aijournal.domain.enums import ClaimEventAction, FeedbackDirection  # noqa: TC001


class ClaimSignaturePayload(StrictModel):
    """Serialized signature describing the target slot for a claim."""

    claim_type: str
    subject: str
    predicate: str
    domain: str | None = None
    context: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)


class ClaimConflictPayload(StrictModel):
    """Structured conflict emitted during consolidation previews."""

    claim_id: str
    signature: ClaimSignaturePayload
    statement: str
    existing_value: str
    incoming_value: str
    incoming_sources: list[ClaimSource] = Field(default_factory=list)


class ClaimPreviewEvent(StrictModel):
    """Outcome of attempting to merge a claim proposal into existing atoms."""

    kind: str = "preview"
    action: ClaimEventAction
    claim_id: str
    delta_strength: float | None = None
    statement: str | None = None
    value: str | None = None
    strength: float | None = None
    signature: ClaimSignaturePayload | None = None
    conflict: ClaimConflictPayload | None = None
    related_claim_id: str | None = None
    related_action: str | None = None
    related_signature: ClaimSignaturePayload | None = None


class FeedbackAdjustmentEvent(StrictModel):
    """Record of a claim strength adjustment triggered by chat feedback."""

    kind: str = "feedback"
    claim_id: str
    old_strength: float
    new_strength: float
    delta: float


ClaimChangeEvent = Annotated[
    ClaimPreviewEvent | FeedbackAdjustmentEvent,
    Field(discriminator="kind"),
]


class FeedbackBatch(StrictModel):
    """Batch of feedback adjustments queued for claim strength updates."""

    batch_id: str
    created_at: str
    session_id: str
    question: str
    feedback: FeedbackDirection
    events: list[FeedbackAdjustmentEvent] = Field(default_factory=list)
