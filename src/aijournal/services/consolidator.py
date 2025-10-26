"""Claim consolidation utilities for merging incoming observations."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _scope_tuple(
    scope: dict[str, Any] | None,
) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    scope = scope or {}
    domain = scope.get("domain")
    context = tuple(str(item).strip() for item in scope.get("context", []) if str(item).strip())
    conditions = tuple(
        str(item).strip() for item in scope.get("conditions", []) if str(item).strip()
    )
    return (str(domain) if domain else None, context, conditions)


@dataclass(frozen=True)
class ClaimSignature:
    """Canonical identifier for matching claims without relying on the claim id."""

    claim_type: str
    subject: str
    predicate: str
    scope: tuple[str | None, tuple[str, ...], tuple[str, ...]]

    @classmethod
    def from_claim(cls, claim: dict[str, Any]) -> ClaimSignature:
        return cls(
            claim_type=str(claim.get("type") or "preference"),
            subject=str(claim.get("subject") or ""),
            predicate=str(claim.get("predicate") or ""),
            scope=_scope_tuple(claim.get("scope")),
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
    incoming_sources: list[dict[str, Any]]


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

    def upsert(self, claims: list[dict[str, Any]], incoming: dict[str, Any]) -> ClaimMergeOutcome:
        signature = ClaimSignature.from_claim(incoming)
        index = self._find_existing_index(claims, signature, incoming.get("id"))

        if index is None:
            self._initialize_provenance(incoming)
            claims.append(incoming)
            return ClaimMergeOutcome(
                changed=True, action="created", claim_id=str(incoming.get("id"))
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
            return ClaimMergeOutcome(
                changed=changed,
                action="merged" if changed else "noop",
                claim_id=str(existing.get("id")),
                delta_strength=delta,
            )

        conflict, delta = self._handle_conflict(existing, incoming, signature)
        return ClaimMergeOutcome(
            changed=conflict is not None,
            action="conflict" if conflict else "noop",
            claim_id=str(existing.get("id")),
            delta_strength=delta if conflict else 0.0,
            conflict=conflict,
        )

    def _find_existing_index(
        self,
        claims: Sequence[dict[str, Any]],
        signature: ClaimSignature,
        incoming_id: Any,
    ) -> int | None:
        incoming_id_str = str(incoming_id) if incoming_id else None
        for idx, claim in enumerate(claims):
            if incoming_id_str and str(claim.get("id")) == incoming_id_str:
                return idx
            if ClaimSignature.from_claim(claim) == signature:
                return idx
        return None

    def _initialize_provenance(self, claim: dict[str, Any]) -> None:
        provenance = claim.setdefault("provenance", {})
        existing_count = provenance.get("observation_count")
        if not isinstance(existing_count, int) or existing_count <= 0:
            provenance["observation_count"] = max(1, len(provenance.get("sources", [])) or 1)
        provenance["last_updated"] = self._timestamp
        if not provenance.get("first_seen"):
            provenance["first_seen"] = self._timestamp.split("T", 1)[0]

    def _values_equal(self, existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
        return str(existing.get("value")) == str(incoming.get("value"))

    def _merge_strength(
        self,
        existing: dict[str, Any],
        incoming: dict[str, Any],
    ) -> tuple[float, bool]:
        prev_strength = _clamp01(float(existing.get("strength", 0.6)))
        signal = _clamp01(float(incoming.get("strength", incoming.get("confidence", 0.6))))

        provenance = existing.setdefault("provenance", {})
        n_prev = provenance.get("observation_count")
        if not isinstance(n_prev, int) or n_prev <= 0:
            n_prev = len(provenance.get("sources", [])) or 1
        w_prev = min(1.0, math.log1p(n_prev))
        w_obs = 1.0
        merged_strength = _clamp01((w_prev * prev_strength + w_obs * signal) / (w_prev + w_obs))

        provenance["observation_count"] = n_prev + 1
        provenance["last_updated"] = self._timestamp
        delta = merged_strength - prev_strength
        if delta:
            existing["strength"] = merged_strength
        else:
            existing["strength"] = prev_strength
        return delta, True

    def _merge_sources(self, existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
        existing_sources = list(existing.get("provenance", {}).get("sources", []))
        incoming_sources = list(incoming.get("provenance", {}).get("sources", []))
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
            existing.setdefault("provenance", {})["sources"] = combined
        return changed

    def _maybe_promote_status(self, existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
        existing_status = str(existing.get("status", "tentative"))
        incoming_status = str(incoming.get("status", existing_status))
        if existing_status == "accepted":
            return False
        if incoming_status == "accepted":
            existing["status"] = "accepted"
            return True
        return False

    def _maybe_upgrade_method(self, existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
        priorities = {"behavioral": 3, "self_report": 2, "inferred": 1}
        existing_method = str(existing.get("method", "inferred"))
        incoming_method = str(incoming.get("method", existing_method))
        if priorities.get(incoming_method, 0) > priorities.get(existing_method, 0):
            existing["method"] = incoming_method
            return True
        return False

    def _propagate_user_verified(self, existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
        if existing.get("user_verified"):
            return False
        if incoming.get("user_verified"):
            existing["user_verified"] = True
            return True
        return False

    def _handle_conflict(
        self,
        existing: dict[str, Any],
        incoming: dict[str, Any],
        signature: ClaimSignature,
    ) -> tuple[ClaimConflict | None, float]:
        prev_strength = _clamp01(float(existing.get("strength", 0.6)))
        new_strength = _clamp01(prev_strength - 0.15)
        changed = False
        if new_strength != prev_strength:
            existing["strength"] = new_strength
            changed = True
        if str(existing.get("status")) != "tentative":
            existing["status"] = "tentative"
            changed = True
        provenance = existing.setdefault("provenance", {})
        count = provenance.get("observation_count")
        if not isinstance(count, int) or count <= 0:
            count = len(provenance.get("sources", [])) or 1
        provenance["observation_count"] = count + 1
        provenance["last_updated"] = self._timestamp
        sources_changed = self._merge_sources(existing, incoming)
        changed = changed or sources_changed
        if not changed:
            return (None, 0.0)
        conflict = ClaimConflict(
            claim_id=str(existing.get("id")),
            signature=signature,
            statement=str(existing.get("statement", "")),
            existing_value=str(existing.get("value", "")),
            incoming_value=str(incoming.get("value", "")),
            incoming_sources=list(incoming.get("provenance", {}).get("sources", [])),
        )
        return (conflict, new_strength - prev_strength)


def _source_key(source: dict[str, Any]) -> tuple[str, tuple[tuple[Any, ...], ...]]:
    entry_id = str(source.get("entry_id", ""))
    spans = source.get("spans") if isinstance(source, dict) else None
    normalized_spans: list[tuple[Any, ...]] = []
    if isinstance(spans, Sequence):
        for span in spans:
            if not isinstance(span, dict):
                continue
            normalized_spans.append(
                (
                    span.get("type"),
                    span.get("index"),
                    span.get("start"),
                    span.get("end"),
                ),
            )
    return (entry_id, tuple(normalized_spans))


__all__ = [
    "ClaimConsolidator",
    "ClaimConflict",
    "ClaimMergeOutcome",
    "ClaimSignature",
]
