"""Claim consolidation utilities for merging incoming observations."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..models import ClaimAtom, ClaimSource, ClaimSourceSpan, Provenance, Scope


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _scope_tuple(scope: Scope | None) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    scope = scope or Scope()
    domain = scope.domain if scope.domain else None
    context = tuple(item.strip() for item in scope.context if item.strip())
    conditions = tuple(item.strip() for item in scope.conditions if item.strip())
    return (domain, context, conditions)


@dataclass(frozen=True)
class ClaimSignature:
    """Canonical identifier for matching claims without relying on the claim id."""

    claim_type: str
    subject: str
    predicate: str
    scope: tuple[str | None, tuple[str, ...], tuple[str, ...]]

    @classmethod
    def from_atom(cls, claim: ClaimAtom) -> ClaimSignature:
        return cls(
            claim_type=claim.type,
            subject=claim.subject,
            predicate=claim.predicate,
            scope=_scope_tuple(claim.scope),
        )

    def as_tuple(self) -> tuple[str, str, str, tuple[str | None, tuple[str, ...], tuple[str, ...]]]:
        return (self.claim_type, self.subject, self.predicate, self.scope)


@dataclass(frozen=True)
class ClaimConflict:
    """Conflict emitted when incoming evidence contradicts an existing claim."""

    claim_id: str
    signature: ClaimSignature
    statement: str
    existing_value: str
    incoming_value: str
    incoming_sources: list[ClaimSource]


@dataclass
class ClaimMergeOutcome:
    """Result of attempting to incorporate a claim observation."""

    changed: bool
    action: str
    claim_id: str
    delta_strength: float = 0.0
    conflict: ClaimConflict | None = None


class ClaimConsolidator:
    """Merge incoming claim atoms into the existing authoritative set."""

    def __init__(self, *, timestamp: str) -> None:
        self._timestamp = timestamp

    def upsert(self, claims: list[Any], incoming: ClaimAtom | dict[str, Any]) -> ClaimMergeOutcome:
        incoming_atom = (
            incoming if isinstance(incoming, ClaimAtom) else ClaimAtom.model_validate(incoming)
        )
        typed_input = all(isinstance(item, ClaimAtom) for item in claims)
        atom_claims = [
            item if isinstance(item, ClaimAtom) else ClaimAtom.model_validate(item)
            for item in claims
        ]
        outcome = self._upsert_atoms(atom_claims, incoming_atom)
        if typed_input:
            claims[:] = atom_claims
        else:
            claims[:] = [claim.model_dump(mode="python") for claim in atom_claims]
        return outcome

    def _upsert_atoms(self, claims: list[ClaimAtom], incoming: ClaimAtom) -> ClaimMergeOutcome:
        signature = ClaimSignature.from_atom(incoming)
        index = self._find_existing_index(claims, signature, incoming.id)

        if index is None:
            self._initialize_provenance(incoming.provenance)
            claims.append(incoming)
            return ClaimMergeOutcome(changed=True, action="created", claim_id=incoming.id)

        existing = claims[index]
        if self._values_equal(existing, incoming):
            delta, observations_changed = self._merge_strength(existing, incoming)
            sources_delta = self._merge_sources(existing, incoming)
            status_changed = self._maybe_promote_status(existing, incoming)
            method_changed = self._maybe_upgrade_method(existing, incoming)
            user_verified_changed = self._propagate_user_verified(existing, incoming)
            changed = any(
                (
                    delta != 0.0,
                    sources_delta,
                    status_changed,
                    method_changed,
                    user_verified_changed,
                    observations_changed,
                ),
            )
            return ClaimMergeOutcome(
                changed=changed,
                action="merged" if changed else "noop",
                claim_id=existing.id,
                delta_strength=delta,
            )

        conflict, delta = self._handle_conflict(existing, incoming, signature)
        return ClaimMergeOutcome(
            changed=conflict is not None,
            action="conflict" if conflict else "noop",
            claim_id=existing.id,
            delta_strength=delta if conflict else 0.0,
            conflict=conflict,
        )

    def _find_existing_index(
        self,
        claims: Sequence[ClaimAtom],
        signature: ClaimSignature,
        incoming_id: str | None,
    ) -> int | None:
        for idx, claim in enumerate(claims):
            if incoming_id and claim.id == incoming_id:
                return idx
            if ClaimSignature.from_atom(claim) == signature:
                return idx
        return None

    def _initialize_provenance(self, provenance: Provenance) -> None:
        if provenance.observation_count <= 0:
            provenance.observation_count = max(1, len(provenance.sources) or 1)
        provenance.last_updated = self._timestamp
        if not provenance.first_seen:
            provenance.first_seen = self._timestamp.split("T", 1)[0]

    def _values_equal(self, existing: ClaimAtom, incoming: ClaimAtom) -> bool:
        return existing.value == incoming.value

    def _merge_strength(
        self,
        existing: ClaimAtom,
        incoming: ClaimAtom,
    ) -> tuple[float, bool]:
        prev_strength = _clamp01(float(existing.strength))
        signal = _clamp01(float(incoming.strength))

        provenance = existing.provenance
        n_prev = provenance.observation_count or len(provenance.sources) or 1
        w_prev = min(1.0, math.log1p(n_prev))
        w_obs = 1.0
        merged_strength = _clamp01((w_prev * prev_strength + w_obs * signal) / (w_prev + w_obs))

        provenance.observation_count = n_prev + 1
        provenance.last_updated = self._timestamp
        delta = merged_strength - prev_strength
        if delta:
            existing.strength = merged_strength
        else:
            existing.strength = prev_strength
        return delta, True

    def _merge_sources(self, existing: ClaimAtom, incoming: ClaimAtom) -> bool:
        existing_sources = list(existing.provenance.sources)
        incoming_sources = list(incoming.provenance.sources)
        combined = list(existing_sources)
        seen = {_source_key(source) for source in existing_sources}
        changed = False
        for source in incoming_sources:
            key = _source_key(source)
            if key in seen:
                continue
            seen.add(key)
            combined.append(source)
            changed = True
        if changed:
            existing.provenance.sources = combined
        return changed

    def _maybe_promote_status(self, existing: ClaimAtom, incoming: ClaimAtom) -> bool:
        if existing.status == "accepted":
            return False
        if incoming.status == "accepted":
            existing.status = "accepted"
            return True
        return False

    def _maybe_upgrade_method(self, existing: ClaimAtom, incoming: ClaimAtom) -> bool:
        priorities = {"behavioral": 3, "self_report": 2, "inferred": 1}
        existing_method = priorities.get(existing.method, 0)
        incoming_method = priorities.get(incoming.method, 0)
        if incoming_method > existing_method:
            existing.method = incoming.method
            return True
        return False

    def _propagate_user_verified(self, existing: ClaimAtom, incoming: ClaimAtom) -> bool:
        if existing.user_verified:
            return False
        if incoming.user_verified:
            existing.user_verified = True
            return True
        return False

    def _handle_conflict(
        self,
        existing: ClaimAtom,
        incoming: ClaimAtom,
        signature: ClaimSignature,
    ) -> tuple[ClaimConflict | None, float]:
        prev_strength = _clamp01(float(existing.strength))
        new_strength = _clamp01(prev_strength - 0.15)
        changed = False
        if new_strength != prev_strength:
            existing.strength = new_strength
            changed = True
        if existing.status != "tentative":
            existing.status = "tentative"
            changed = True

        provenance = existing.provenance
        count = provenance.observation_count or len(provenance.sources) or 1
        provenance.observation_count = count + 1
        provenance.last_updated = self._timestamp
        sources_changed = self._merge_sources(existing, incoming)
        changed = changed or sources_changed
        if not changed:
            return (None, 0.0)
        conflict = ClaimConflict(
            claim_id=existing.id,
            signature=signature,
            statement=existing.statement,
            existing_value=existing.value,
            incoming_value=incoming.value,
            incoming_sources=list(incoming.provenance.sources),
        )
        return (conflict, new_strength - prev_strength)


def _source_key(
    source: ClaimSource,
) -> tuple[str, tuple[tuple[str | None, int | None, int | None, int | None], ...]]:
    def span_key(span: ClaimSourceSpan) -> tuple[str | None, int | None, int | None, int | None]:
        return (span.type, span.index, span.start, span.end)

    return (source.entry_id, tuple(span_key(span) for span in source.spans))


__all__ = [
    "ClaimConsolidator",
    "ClaimConflict",
    "ClaimMergeOutcome",
    "ClaimSignature",
]
