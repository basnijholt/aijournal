"""Shared helpers for building/printing claim preview data."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from aijournal.domain.claims import ClaimAtom, ClaimSource, Scope
from aijournal.domain.enums import ClaimEventAction
from aijournal.domain.events import (
    ClaimConflictPayload,
    ClaimPreviewEvent,
    ClaimSignaturePayload,
)
from aijournal.domain.evidence import redact_source_text
from aijournal.models.derived import ProfileUpdatePreview
from aijournal.services.consolidator import (
    ClaimConflict,
    ClaimConsolidator,
    ClaimMergeOutcome,
    ClaimSignature,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aijournal.domain.changes import ClaimProposal


def claim_proposal_to_atom(proposal: ClaimProposal, *, timestamp: str) -> ClaimAtom:
    evidence_sources = [
        ClaimSource.model_validate(redact_source_text(source).model_dump(mode="python"))
        for source in proposal.evidence
    ]
    claim_payload = proposal.model_dump(
        mode="python",
        exclude={"normalized_ids", "manifest_hashes", "evidence", "rationale"},
    )
    claim_payload["provenance"] = {
        "sources": [source.model_dump(mode="python") for source in evidence_sources],
        "first_seen": timestamp.split("T", 1)[0],
        "last_updated": timestamp,
        "observation_count": max(1, len(evidence_sources) or 1),
    }

    from aijournal.pipelines import normalization

    return normalization.normalize_claim_atom(
        claim_payload,
        timestamp=timestamp,
        default_sources=evidence_sources,
    )


def signature_payload_from_claim(claim: ClaimAtom) -> ClaimSignaturePayload:
    scope = claim.scope or Scope()
    return ClaimSignaturePayload(
        claim_type=str(claim.type or "preference"),
        subject=str(claim.subject or ""),
        predicate=str(claim.predicate or ""),
        domain=scope.domain,
        context=[item for item in scope.context if item],
        conditions=[item for item in scope.conditions if item],
    )


def signature_payload_from_signature(signature: ClaimSignature) -> ClaimSignaturePayload:
    domain, context, conditions = signature.scope
    return ClaimSignaturePayload(
        claim_type=signature.claim_type,
        subject=signature.subject,
        predicate=signature.predicate,
        domain=domain,
        context=[item for item in context if item],
        conditions=[item for item in conditions if item],
    )


def conflict_payload_from_outcome(conflict: ClaimConflict) -> ClaimConflictPayload:
    return ClaimConflictPayload(
        claim_id=conflict.claim_id,
        signature=signature_payload_from_signature(conflict.signature),
        statement=conflict.statement,
        existing_value=conflict.existing_value,
        incoming_value=conflict.incoming_value,
        incoming_sources=[source.model_copy(deep=True) for source in conflict.incoming_sources],
    )


def format_scope_label(scope: tuple[str | None, tuple[str, ...], tuple[str, ...]]) -> str:
    domain, context, conditions = scope
    parts: list[str] = []
    if domain:
        parts.append(str(domain))
    if context:
        parts.append("/".join(context))
    if conditions:
        parts.append("|".join(conditions))
    return " :: ".join(parts) if parts else "global"


def scope_tuple_from_payload(
    signature: ClaimSignaturePayload | None,
) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    if signature is None:
        return (None, (), ())
    return (
        signature.domain,
        tuple(signature.context),
        tuple(signature.conditions),
    )


def build_claim_preview(
    claim_proposals: Sequence[ClaimProposal],
    existing_claims: Sequence[ClaimAtom],
    *,
    timestamp: str,
) -> ProfileUpdatePreview | None:
    if not claim_proposals:
        return None

    working_claims = [claim.model_copy(deep=True) for claim in existing_claims]
    consolidator = ClaimConsolidator(timestamp=timestamp)
    events: list[ClaimPreviewEvent] = []
    prompts: list[str] = []

    for proposal in claim_proposals:
        incoming = claim_proposal_to_atom(proposal, timestamp=timestamp)
        outcome = consolidator.upsert(working_claims, incoming)
        if not outcome.changed:
            continue
        signature_payload = (
            signature_payload_from_signature(outcome.signature)
            if outcome.signature
            else signature_payload_from_claim(incoming)
        )
        related_signature_payload = (
            signature_payload_from_signature(outcome.related_signature)
            if outcome.related_signature
            else None
        )
        conflict_payload = None
        if outcome.conflict:
            conflict_payload = conflict_payload_from_outcome(outcome.conflict)
            scope_label = format_scope_label(outcome.conflict.signature.scope)
            prompts.append(
                f"Clarify claim {outcome.claim_id} [{scope_label}]: "
                f"existing='{outcome.conflict.existing_value}' vs "
                f"incoming='{outcome.conflict.incoming_value}'.",
            )
        events.append(
            ClaimPreviewEvent(
                action=ClaimEventAction(outcome.action),
                claim_id=outcome.claim_id,
                delta_strength=float(outcome.delta_strength or 0.0),
                statement=incoming.statement,
                value=incoming.value,
                strength=float(incoming.strength or 0.0),
                signature=signature_payload,
                conflict=conflict_payload,
                related_claim_id=outcome.related_claim_id,
                related_action=outcome.related_action,
                related_signature=related_signature_payload,
            ),
        )

    if not events and not prompts:
        return None
    return ProfileUpdatePreview(claim_events=events, interview_prompts=prompts)


def emit_claim_merge_events(events: Sequence[ClaimMergeOutcome], heading: str) -> None:
    relevant = [event for event in events if event.changed]
    if not relevant:
        return
    typer.echo(heading)
    for event in relevant:
        if event.action == "upsert":
            typer.echo(f"  • new claim {event.claim_id}")
        elif event.action == "update":
            note = f" (Δstrength {event.delta_strength:+0.2f})" if event.delta_strength else ""
            typer.echo(f"  • updated {event.claim_id}{note}")
        elif event.action == "strength_delta":
            typer.echo(
                f"  • strength adjusted {event.claim_id} (Δ {event.delta_strength:+0.2f})",
            )
        elif event.action == "conflict" and event.conflict:
            conflict = event.conflict
            scope_label = format_scope_label(conflict.signature.scope)
            typer.secho(
                (
                    f"  • conflict {event.claim_id} [{scope_label}]: "
                    f"'{conflict.existing_value}' vs '{conflict.incoming_value}'"
                ),
                fg=typer.colors.YELLOW,
            )
            if event.related_claim_id and event.related_signature:
                related_scope = format_scope_label(event.related_signature.scope)
                action_note = f" ({event.related_action})" if event.related_action else ""
                typer.echo(
                    f"    ↳ spawned {event.related_claim_id} [{related_scope}]{action_note}",
                )
        elif event.action == "delete":
            typer.echo(f"  • deleted {event.claim_id}")


__all__ = [
    "build_claim_preview",
    "claim_proposal_to_atom",
    "conflict_payload_from_outcome",
    "emit_claim_merge_events",
    "format_scope_label",
    "scope_tuple_from_payload",
    "signature_payload_from_claim",
    "signature_payload_from_signature",
]
