"""Normalization helpers extracted from the CLI monolith."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from aijournal.domain.claims import (
    ClaimAtom,
    ClaimSource,
    ClaimSourceSpan,
    Provenance,
    Scope,
)
from aijournal.domain.enums import ClaimMethod, ClaimStatus, ClaimType
from aijournal.domain.evidence import redact_source_text
from aijournal.utils import time as time_utils
from aijournal.utils.coercion import coerce_float, coerce_int

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from aijournal.domain.journal import Section as IngestSection
    from aijournal.ingest_agent import IngestResult


def normalize_status(value: str | None) -> ClaimStatus:
    status = (value or "tentative").strip().lower()
    if status not in {"accepted", "tentative", "rejected"}:
        status = "tentative"
    return ClaimStatus(status)


def _clamp_strength(value: float | None, default: float = 0.6) -> float:
    try:
        strength = float(value) if value is not None else default
    except (TypeError, ValueError):
        strength = default
    return max(0.0, min(1.0, strength))


def normalize_created_at(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value.astimezone(UTC)
        return time_utils.format_timestamp(dt)

    if isinstance(value, str):
        candidate = value.replace("Z", "+00:00") if value.endswith("Z") else value
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return time_utils.format_timestamp(dt)
        except ValueError:
            return value

    return str(value)


def normalize_tags(raw: Iterable[Any]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        slug = time_utils.slugify_title(text)
        if slug and slug not in seen:
            seen.add(slug)
            tags.append(slug)
    return tags


def normalize_scope(raw: Any) -> Scope:
    if isinstance(raw, Scope):
        return raw.model_copy(deep=True)

    scope_dict = raw if isinstance(raw, dict) else {}
    domain_raw = scope_dict.get("domain")
    domain = str(domain_raw).strip() if isinstance(domain_raw, str) and domain_raw.strip() else None

    def _string_list(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        sanitized: list[str] = []
        for item in values:
            text = str(item).strip()
            if text:
                sanitized.append(text)
        return sanitized

    context = _string_list(scope_dict.get("context"))
    conditions = _string_list(scope_dict.get("conditions"))
    return Scope(domain=domain, context=context, conditions=conditions)


def normalize_sources(raw: Any) -> list[ClaimSource]:
    sources: list[ClaimSource] = []
    if not isinstance(raw, list):
        return sources
    for source in raw:
        if isinstance(source, ClaimSource):
            sanitized = ClaimSource.model_validate(
                redact_source_text(source).model_dump(mode="python"),
            )
            sources.append(sanitized)
            continue
        if not isinstance(source, dict):
            continue
        entry_id = source.get("entry_id")
        if not entry_id:
            continue
        spans_raw = source.get("spans")
        spans: list[ClaimSourceSpan] = []
        if isinstance(spans_raw, list):
            for span in spans_raw:
                if isinstance(span, ClaimSourceSpan):
                    spans.append(span.model_copy(deep=True))
                    continue
                if not isinstance(span, dict):
                    continue
                spans.append(
                    ClaimSourceSpan(
                        type=str(span.get("type") or "excerpt"),
                        index=coerce_int(span.get("index")),
                        start=coerce_int(span.get("start")),
                        end=coerce_int(span.get("end")),
                    ),
                )
        source_obj = ClaimSource(entry_id=str(entry_id), spans=spans)
        sanitized = ClaimSource.model_validate(
            redact_source_text(source_obj).model_dump(mode="python"),
        )
        sources.append(sanitized)
    return sources


def _default_claim_sources(raw: ClaimAtom | dict[str, Any]) -> list[ClaimSource]:
    claim_id: str | None
    if isinstance(raw, ClaimAtom):
        claim_id = raw.id
    elif isinstance(raw, dict):
        claim_id_raw = raw.get("id")
        claim_id = str(claim_id_raw) if claim_id_raw else None
    else:
        claim_id = None
    if not claim_id:
        return []
    claim_id_str = str(claim_id)
    source = ClaimSource(entry_id=claim_id_str, spans=[])
    sanitized = ClaimSource.model_validate(
        redact_source_text(source).model_dump(mode="python"),
    )
    return [sanitized]


def _coerce_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value.astimezone(UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(value)
    return text if text else None


def normalize_provenance(
    raw: Any,
    *,
    timestamp: str,
    default_sources: Sequence[ClaimSource] | None,
) -> Provenance:
    if isinstance(raw, Provenance):
        provenance = raw.model_copy(deep=True)
    else:
        data = raw if isinstance(raw, dict) else {}
        sources = normalize_sources(data.get("sources"))
        observation_count = coerce_int(data.get("observation_count"))
        first_seen_coerced = _coerce_timestamp(data.get("first_seen"))
        last_updated_coerced = _coerce_timestamp(data.get("last_updated")) or timestamp
        provenance = Provenance(
            sources=sources,
            first_seen=time_utils.created_date(first_seen_coerced or timestamp),
            last_updated=last_updated_coerced,
            observation_count=observation_count
            if observation_count and observation_count > 0
            else max(1, len(sources) or 1),
        )

    if (not provenance.sources) and default_sources:
        provenance.sources = [
            ClaimSource.model_validate(
                redact_source_text(source).model_dump(mode="python"),
            )
            for source in default_sources
        ]

    first_seen_ts = _coerce_timestamp(provenance.first_seen)
    provenance.first_seen = (
        time_utils.created_date(first_seen_ts)
        if first_seen_ts
        else time_utils.created_date(timestamp)
    )
    provenance.last_updated = _coerce_timestamp(provenance.last_updated) or timestamp
    if provenance.observation_count <= 0:
        provenance.observation_count = max(1, len(provenance.sources) or 1)
    provenance.sources = [
        ClaimSource.model_validate(
            redact_source_text(source).model_dump(mode="python"),
        )
        for source in provenance.sources
    ]
    return provenance


def normalize_claim_atom(
    data: ClaimAtom | dict[str, Any],
    *,
    timestamp: str,
    default_sources: Sequence[ClaimSource] | None = None,
) -> ClaimAtom:
    if default_sources is None:
        default_sources = _default_claim_sources(data)

    if isinstance(data, ClaimAtom):
        base = data.model_dump(mode="python")
    elif hasattr(data, "model_dump"):
        base = data.model_dump(mode="python")  # type: ignore[call-arg]
    else:
        base = dict(data)

    statement = str(base.get("statement") or "").strip()
    if not statement:
        msg = "Claim statement is required"
        raise ValueError(msg)

    claim_type_raw = str(base.get("type") or "preference").strip().lower()
    valid_types = {item.value for item in ClaimType}
    claim_type_value = (
        claim_type_raw if claim_type_raw in valid_types else ClaimType.PREFERENCE.value
    )
    claim_type = ClaimType(claim_type_value)

    subject_candidate = (
        base.get("subject")
        or base.get("id")
        or time_utils.slugify_title(statement)
        or "observation"
    )
    subject = str(subject_candidate).strip() or "observation"

    predicate = str(base.get("predicate") or "statement").strip() or "statement"

    value_raw = base.get("value")
    value = (
        str(value_raw).strip() if value_raw is not None and str(value_raw).strip() else statement
    )

    claim_id_raw = base.get("id")
    claim_id = str(claim_id_raw).strip() or None if claim_id_raw else None
    if not claim_id:
        subject_slug = time_utils.slugify_title(subject) or "subject"
        predicate_slug = time_utils.slugify_title(predicate) or "predicate"
        claim_id = f"{claim_type}.{subject_slug}.{predicate_slug}"
    claim_id = claim_id[:96]

    scope = normalize_scope(base.get("scope"))

    strength_value = base.get("strength", base.get("confidence"))
    strength_numeric = coerce_float(strength_value)
    strength = max(0.0, min(1.0, strength_numeric if strength_numeric is not None else 0.6))

    status_raw = str(base.get("status") or "tentative").strip().lower()
    valid_status = {item.value for item in ClaimStatus}
    status_value = status_raw if status_raw in valid_status else ClaimStatus.TENTATIVE.value
    status = ClaimStatus(status_value)

    method_raw = str(base.get("method") or "inferred").strip().lower()
    valid_methods = {item.value for item in ClaimMethod}
    method_value = method_raw if method_raw in valid_methods else ClaimMethod.INFERRED.value
    method = ClaimMethod(method_value)

    user_verified = bool(base.get("user_verified", False))
    review_after_days = coerce_int(base.get("review_after_days")) or 120

    provenance = normalize_provenance(
        base.get("provenance"),
        timestamp=timestamp,
        default_sources=default_sources,
    )

    return ClaimAtom(
        id=claim_id,
        type=claim_type,
        subject=subject,
        predicate=predicate,
        value=value,
        statement=statement,
        scope=scope,
        strength=strength,
        status=status,
        method=method,
        user_verified=user_verified,
        review_after_days=review_after_days,
        provenance=provenance,
    )


def clean_summary(text: str | None, fallback: str | None = None) -> str | None:
    candidate = (text or "").strip()
    if candidate:
        for marker in (',"entry_id"', ',"tags"', ',"sections"'):
            idx = candidate.find(marker)
            if idx != -1:
                candidate = candidate[:idx]
                break
        candidate = candidate.replace("\n", " ").strip().strip('"')
        sentences = re.split(r"(?<=[.!?])\s+", candidate)
        sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
        candidate = " ".join(sentences[:2]) if sentences else ""

    if not candidate and fallback:
        candidate = fallback.strip()

    return candidate or None


def merge_sections(
    primary: Iterable[IngestSection],
    fallback: Iterable[dict[str, Any]],
    *,
    title: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_section(heading: str, level: int, summary: str | None = None) -> None:
        heading = heading.strip()
        if not heading:
            return
        key = heading.lower()
        if key in seen:
            return
        seen.add(key)
        entry: dict[str, Any] = {
            "heading": heading,
            "level": max(1, min(6, int(level or 1))),
        }
        if summary:
            entry["summary"] = summary.strip()
        entries.append(entry)

    for primary_section in primary:
        add_section(primary_section.heading, primary_section.level, primary_section.summary)
        if len(entries) >= limit:
            return entries

    for fallback_section in fallback:
        heading = str(fallback_section.get("heading") or title)
        level = int(fallback_section.get("level", 2))
        add_section(heading, level)
        if len(entries) >= limit:
            return entries

    if not entries:
        add_section(title or "entry", 1)
    return entries


def _sanitize_entry_id(candidate: str | None, title: str, date_str: str, digest: str) -> str:
    slug = ""
    if candidate and candidate.strip():
        slug = time_utils.slugify_title(candidate)
    elif title.strip():
        slug = time_utils.slugify_title(title)

    if slug:
        if not slug.startswith(date_str):
            slug = f"{date_str}-{slug}"
    else:
        slug = f"{date_str}-{digest[:8]}"

    return slug[:96]


def normalized_from_structured(
    structured: IngestResult,
    *,
    source_path: str,
    root: Any,  # retained for signature parity with CLI helper
    digest: str,
    source_type: str,
    fallback_sections: list[dict[str, Any]] | None = None,
    fallback_tags: list[str] | None = None,
    fallback_summary: str | None = None,
) -> tuple[dict[str, Any], str]:
    _ = root  # kept for compatibility with original helper signature
    created_at = structured.created_at
    if isinstance(created_at, datetime):
        created_str = time_utils.format_timestamp(created_at.astimezone(UTC))
    else:
        created_str = normalize_created_at(created_at)

    date_str = time_utils.created_date(created_str)
    entry_id = _sanitize_entry_id(structured.entry_id, structured.title, date_str, digest)
    tags = normalize_tags(list(structured.tags or []) + list(fallback_tags or []))

    merged_sections = merge_sections(
        structured.sections or [],
        fallback_sections or [],
        title=structured.title.strip() or entry_id,
    )

    normalized = {
        "id": entry_id,
        "created_at": created_str,
        "source_path": source_path,
        "title": structured.title.strip() or entry_id,
        "tags": tags,
        "sections": merged_sections,
        "source_hash": digest,
        "source_type": source_type,
    }
    summary = clean_summary(structured.summary, fallback_summary)
    if summary:
        normalized["summary"] = summary

    return normalized, date_str
