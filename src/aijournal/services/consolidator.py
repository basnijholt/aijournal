"""Claim consolidation utilities for merging incoming observations."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict, Field

from aijournal.common.base import StrictModel
from aijournal.domain.claims import ClaimAtom, ClaimSource, ClaimSourceSpan, Provenance, Scope
from aijournal.domain.enums import ClaimMethod, ClaimStatus
from aijournal.domain.evidence import redact_source_text

if TYPE_CHECKING:
    from collections.abc import Sequence


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _redacted_sources(sources: Sequence[ClaimSource]) -> list[ClaimSource]:
    return [redact_source_text(source) for source in sources]


def _redacted_provenance(provenance: Provenance, timestamp: str) -> Provenance:
    redacted = provenance.model_copy(update={"sources": _redacted_sources(provenance.sources)})
    redacted.last_updated = timestamp
    if redacted.observation_count <= 0:
        redacted.observation_count = max(1, len(redacted.sources) or 1)
    if not redacted.first_seen:
        redacted.first_seen = timestamp.split("T", 1)[0]
    return redacted


def _scope_tuple(scope: Scope | None) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    scope = scope or Scope()
    domain = scope.domain if scope.domain else None
    context = tuple(item.strip() for item in scope.context if item.strip())
    conditions = tuple(item.strip() for item in scope.conditions if item.strip())
    return (domain, context, conditions)


_SCOPE_QUALIFIER_GROUPS: dict[str, dict[str, tuple[str, ...]]] = {
    "day_type": {
        "weekday": ("weekday", "weekdays", "workday", "workdays"),
        "weekend": ("weekend", "weekends"),
    },
    "company": {
        "solo": ("solo", "independent", "individual", "alone"),
        "team": ("team", "collaborative", "pair", "paired", "pairing", "group"),
    },
}

_SCOPE_COMPLEMENTS: dict[str, str] = {
    "weekday": "weekend",
    "weekend": "weekday",
    "solo": "team",
    "team": "solo",
}


class ClaimSignature(StrictModel):
    model_config = ConfigDict(frozen=True)
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


class ClaimConflict(StrictModel):
    model_config = ConfigDict(frozen=True)
    """Conflict emitted when incoming evidence contradicts an existing claim."""

    claim_id: str
    signature: ClaimSignature
    statement: str
    existing_value: str
    incoming_value: str
    incoming_sources: list[ClaimSource] = Field(default_factory=list)


class ClaimMergeOutcome(StrictModel):
    """Result of attempting to incorporate a claim observation."""

    changed: bool
    action: str
    claim_id: str
    delta_strength: float = 0.0
    conflict: ClaimConflict | None = None
    related_claim_id: str | None = None
    related_action: str | None = None
    signature: ClaimSignature | None = None
    related_signature: ClaimSignature | None = None


class ClaimConsolidator:
    """Merge incoming claim atoms into the existing authoritative set."""

    def __init__(self, *, timestamp: str) -> None:
        self._timestamp = timestamp

    def upsert(self, claims: list[Any], incoming: ClaimAtom | dict[str, Any]) -> ClaimMergeOutcome:
        incoming_atom = (
            incoming if isinstance(incoming, ClaimAtom) else ClaimAtom.model_validate(incoming)
        )
        incoming_atom = incoming_atom.model_copy(
            update={
                "provenance": _redacted_provenance(
                    incoming_atom.provenance,
                    timestamp=self._timestamp,
                ),
            },
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
            return ClaimMergeOutcome(
                changed=True,
                action="upsert",
                claim_id=incoming.id,
                signature=signature,
            )

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
            structural_change = any(
                (
                    sources_delta,
                    status_changed,
                    method_changed,
                    user_verified_changed,
                ),
            )
            action = "strength_delta" if (delta != 0.0 and not structural_change) else "update"
            if not changed:
                action = "update"
            return ClaimMergeOutcome(
                changed=changed,
                action=action,
                claim_id=existing.id,
                delta_strength=delta,
                signature=ClaimSignature.from_atom(existing),
            )

        return self._handle_conflict(claims, index, existing, incoming, signature)

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
        provenance.sources = _redacted_sources(provenance.sources)
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
        incoming_sources = _redacted_sources(incoming.provenance.sources)
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
        if existing.status == ClaimStatus.ACCEPTED:
            return False
        if incoming.status == ClaimStatus.ACCEPTED:
            existing.status = ClaimStatus.ACCEPTED
            return True
        return False

    def _maybe_upgrade_method(self, existing: ClaimAtom, incoming: ClaimAtom) -> bool:
        priorities = {
            ClaimMethod.BEHAVIORAL.value: 3,
            ClaimMethod.SELF_REPORT.value: 2,
            ClaimMethod.INFERRED.value: 1,
        }
        existing_method = priorities.get(str(existing.method), 0)
        incoming_method = priorities.get(str(incoming.method), 0)
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
        claims: list[ClaimAtom],
        index: int,
        existing: ClaimAtom,
        incoming: ClaimAtom,
        signature: ClaimSignature,
    ) -> ClaimMergeOutcome:
        scoped_outcome = self._attempt_scope_split(claims, existing, incoming)
        if scoped_outcome is not None:
            return scoped_outcome

        prev_strength = _clamp01(float(existing.strength))
        new_strength = _clamp01(prev_strength - 0.15)
        changed = False
        if new_strength != prev_strength:
            existing.strength = new_strength
            changed = True
        if existing.status != ClaimStatus.TENTATIVE:
            existing.status = ClaimStatus.TENTATIVE
            changed = True

        provenance = existing.provenance
        count = provenance.observation_count or len(provenance.sources) or 1
        provenance.observation_count = count + 1
        provenance.last_updated = self._timestamp
        sources_changed = self._merge_sources(existing, incoming)
        changed = changed or sources_changed
        if not changed:
            return ClaimMergeOutcome(
                changed=False,
                action="update",
                claim_id=existing.id,
                delta_strength=0.0,
                conflict=None,
                signature=ClaimSignature.from_atom(existing),
            )

        conflict = ClaimConflict(
            claim_id=existing.id,
            signature=ClaimSignature.from_atom(existing),
            statement=existing.statement,
            existing_value=existing.value,
            incoming_value=incoming.value,
            incoming_sources=list(incoming.provenance.sources),
        )
        return ClaimMergeOutcome(
            changed=True,
            action="conflict",
            claim_id=existing.id,
            delta_strength=new_strength - prev_strength,
            conflict=conflict,
            signature=conflict.signature,
        )

    def _attempt_scope_split(
        self,
        claims: list[ClaimAtom],
        existing: ClaimAtom,
        incoming: ClaimAtom,
    ) -> ClaimMergeOutcome | None:
        for keyword_map in _SCOPE_QUALIFIER_GROUPS.values():
            existing_label = self._detect_scope_label(existing, keyword_map)
            incoming_label = self._detect_scope_label(incoming, keyword_map)

            target_existing = existing_label
            target_incoming = incoming_label
            if target_existing == target_incoming and target_existing is not None:
                continue

            if target_existing is None and target_incoming is None:
                continue

            if target_existing is None and target_incoming is not None:
                target_existing = _SCOPE_COMPLEMENTS.get(target_incoming)
            elif target_incoming is None and target_existing is not None:
                target_incoming = _SCOPE_COMPLEMENTS.get(target_existing)

            if (
                target_existing is None
                or target_incoming is None
                or target_existing == target_incoming
            ):
                continue

            if existing.scope is None:
                existing.scope = Scope()
            if incoming.scope is None:
                incoming.scope = Scope()

            self._ensure_scope_label(existing.scope, target_existing)
            existing.provenance.last_updated = self._timestamp

            scoped_claim = incoming.model_copy(deep=True)
            self._ensure_scope_label(scoped_claim.scope, target_incoming)
            scoped_claim.provenance.last_updated = self._timestamp
            scoped_claim.provenance.observation_count = max(
                1,
                scoped_claim.provenance.observation_count or 1,
            )
            scoped_claim.id = self._unique_scoped_id(claims, scoped_claim.id, target_incoming)

            scoped_outcome = self._upsert_atoms(claims, scoped_claim)
            related_signature = scoped_outcome.signature or ClaimSignature.from_atom(scoped_claim)
            conflict = ClaimConflict(
                claim_id=existing.id,
                signature=ClaimSignature.from_atom(existing),
                statement=existing.statement,
                existing_value=existing.value,
                incoming_value=incoming.value,
                incoming_sources=list(incoming.provenance.sources),
            )
            return ClaimMergeOutcome(
                changed=True,
                action="conflict",
                claim_id=existing.id,
                delta_strength=scoped_outcome.delta_strength if scoped_outcome.changed else 0.0,
                conflict=conflict,
                related_claim_id=scoped_outcome.claim_id,
                related_action=scoped_outcome.action,
                signature=conflict.signature,
                related_signature=related_signature,
            )
        return None

    def _detect_scope_label(
        self,
        claim: ClaimAtom,
        keyword_map: dict[str, tuple[str, ...]],
    ) -> str | None:
        scope = claim.scope or Scope()
        context_lower = {item.lower() for item in scope.context}
        condition_lower = {item.lower() for item in scope.conditions}
        for label, keywords in keyword_map.items():
            keyword_set = {word.lower() for word in keywords}
            if context_lower & keyword_set:
                return label
            if condition_lower & keyword_set:
                return label

        for text in (claim.statement, claim.value):
            if not text:
                continue
            lowered = text.lower()
            for label, keywords in keyword_map.items():
                if any(word in lowered for word in keywords):
                    return label
        return None

    def _ensure_scope_label(self, scope: Scope, label: str) -> None:
        normalized = (label or "").strip()
        if not normalized:
            return
        existing = list(scope.context or [])
        lower_map = {item.lower(): idx for idx, item in enumerate(existing)}
        if normalized.lower() in lower_map:
            existing[lower_map[normalized.lower()]] = normalized
        else:
            existing.append(normalized)
        scope.context = existing

    def _unique_scoped_id(self, claims: Sequence[ClaimAtom], base_id: str, label: str) -> str:
        existing_ids = {claim.id for claim in claims}
        if base_id not in existing_ids:
            return base_id[:96]

        suffix = label.replace(" ", "-").replace("/", "-").lower() or "scope"
        candidate = f"{base_id}.{suffix}"[:96]
        counter = 1
        while candidate in existing_ids:
            candidate = f"{base_id}.{suffix}-{counter}"[:96]
            counter += 1
        return candidate


def _source_key(
    source: ClaimSource,
) -> tuple[str, tuple[tuple[str | None, int | None, int | None, int | None], ...]]:
    def span_key(span: ClaimSourceSpan) -> tuple[str | None, int | None, int | None, int | None]:
        return (span.type, span.index, span.start, span.end)

    return (source.entry_id, tuple(span_key(span) for span in source.spans))
