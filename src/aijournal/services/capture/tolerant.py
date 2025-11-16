"""Tolerant front-matter and date parsing utilities for human-written input.

This module provides robust parsing that never raises exceptions for malformed input,
instead returning partial results with warnings. Designed to handle common variations
in date formats and front-matter syntax that occur in real-world journal entries.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import yaml

from aijournal.utils.text import strip_invisible_prefix

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


FrontMatterFormat = Literal["yaml", "toml", "json", "none"]


class ParseResult:
    """Result of a tolerant parse operation with optional warnings."""

    def __init__(
        self,
        data: dict[str, object],
        body: str,
        format: FrontMatterFormat,
        warnings: list[str] | None = None,
    ) -> None:
        self.data = data
        self.body = body
        self.format = format
        self.warnings = warnings or []

    def add_warning(self, message: str) -> None:
        """Add a warning message to the result."""
        self.warnings.append(message)
        logger.warning(message)


class DateParseResult:
    """Result of a tolerant date parse operation."""

    def __init__(
        self,
        dt: datetime,
        format_used: str,
        warnings: list[str] | None = None,
    ) -> None:
        self.dt = dt
        self.format_used = format_used
        self.warnings = warnings or []

    def add_warning(self, message: str) -> None:
        """Add a warning message to the result."""
        self.warnings.append(message)
        logger.warning(message)


def split_frontmatter_tolerant(text: str) -> ParseResult:
    """Parse front-matter from markdown text, never raising exceptions.

    Supports YAML (---), TOML (+++), and JSON ({...}) delimiters.
    Falls back gracefully when front-matter is missing or malformed.

    Args:
        text: Raw markdown text that may contain front-matter

    Returns:
        ParseResult with metadata dict, body text, detected format, and any warnings

    """
    text = strip_invisible_prefix(text)
    stripped = strip_invisible_prefix(text.lstrip())
    if not stripped:
        return ParseResult({}, "", "none", ["Empty input text"])

    # Try JSON format first (starts with '{' or '[')
    if stripped.startswith(("{", "[")):
        return _parse_json_frontmatter(stripped)

    # Try YAML (---) or TOML (+++)
    if stripped.startswith("---"):
        return _parse_delimited_frontmatter(stripped, "---", "yaml")
    if stripped.startswith("+++"):
        return _parse_delimited_frontmatter(stripped, "+++", "toml")

    # No front-matter detected
    return ParseResult({}, text, "none", ["No front-matter delimiter found"])


def _parse_json_frontmatter(text: str) -> ParseResult:
    """Parse JSON front-matter block."""
    try:
        block, body = _extract_json_block(text)
        data = json.loads(block)
        if not isinstance(data, dict):
            return ParseResult(
                {},
                text,
                "json",
                ["JSON front-matter is not a dictionary, ignoring"],
            )
        return ParseResult(data, body.lstrip("\n"), "json")
    except ValueError as exc:
        return ParseResult(
            {},
            text,
            "json",
            [f"Failed to parse JSON front-matter: {exc}"],
        )
    except json.JSONDecodeError as exc:
        return ParseResult(
            {},
            text,
            "json",
            [f"Invalid JSON syntax in front-matter: {exc}"],
        )


def _extract_json_block(text: str) -> tuple[str, str]:
    """Extract a JSON block from the start of text."""
    depth = 0
    in_string = False
    escape = False
    start_index = None

    for index, char in enumerate(text):
        if start_index is None:
            if char.isspace():
                continue
            if char not in ("{", "["):
                msg = "JSON front-matter must start with '{' or '['"
                raise ValueError(msg)
            start_index = index
            depth = 1
            continue

        if in_string:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char in ("{", "["):
            depth += 1
            continue
        if char in ("}", "]"):
            depth -= 1
            if depth == 0:
                block = text[start_index : index + 1]
                remainder = text[index + 1 :]
                return block, remainder

    msg = "Unterminated JSON front-matter block"
    raise ValueError(msg)


def _parse_delimited_frontmatter(
    text: str,
    delimiter: str,
    format: Literal["yaml", "toml"],
) -> ParseResult:
    """Parse YAML or TOML front-matter with delimiter."""
    parts = text.split(delimiter, 2)
    if len(parts) < 3:
        return ParseResult(
            {},
            text,
            format,
            [f"Incomplete {format.upper()} front-matter block (missing closing delimiter)"],
        )

    frontmatter_raw = parts[1].strip()
    body = parts[2].lstrip("\n")

    if not frontmatter_raw:
        return ParseResult({}, body, format, ["Empty front-matter block"])

    try:
        # Note: For TOML, we're using yaml.safe_load which accepts a subset
        # For production use with strict TOML, consider tomli/tomllib
        data = yaml.safe_load(frontmatter_raw)

        if data is None:
            return ParseResult({}, body, format, ["Front-matter parsed as null/empty"])

        if not isinstance(data, dict):
            return ParseResult(
                {},
                body,  # Return body, not full text
                format,
                [f"{format.upper()} front-matter is not a dictionary"],
            )

        # Validate and warn about unknown keys (optional, could be removed)
        result = ParseResult(data, body, format)
        _validate_frontmatter_keys(data, result)
        return result

    except yaml.YAMLError as exc:
        return ParseResult(
            {},
            text,
            format,
            [f"Failed to parse {format.upper()} front-matter: {exc}"],
        )


def _validate_frontmatter_keys(data: dict[str, object], result: ParseResult) -> None:
    """Validate front-matter keys and add warnings for unknown fields."""
    known_keys = {
        "id",
        "slug",
        "title",
        "created_at",
        "tags",
        "projects",
        "summary",
        "mood",
        "origin",
        "sections",
    }
    unknown = set(data.keys()) - known_keys
    if unknown:
        result.add_warning(
            f"Unknown front-matter keys (will be preserved): {', '.join(sorted(unknown))}",
        )


def parse_date_tolerant(
    value: object,
    fallback: datetime | None = None,
) -> DateParseResult:
    """Parse a date/datetime value tolerantly, handling common formats.

    Supports:
    - ISO 8601: "2025-01-05T10:30:00Z", "2025-01-05"
    - Common text: "Jan 5, 2024", "January 5, 2024", "5 Jan 2024"
    - Slashed: "1/5/2024", "01/05/2024"
    - Dashed variations: "2024-1-5", "2024-01-5"
    - datetime objects
    - date-like objects with year/month/day attributes

    Args:
        value: The value to parse (str, datetime, date-like object, etc.)
        fallback: Datetime to return if parsing fails completely

    Returns:
        DateParseResult with parsed datetime (UTC), format used, and warnings

    """
    warnings: list[str] = []

    # Handle None or missing value
    if value is None:
        if fallback is not None:
            return DateParseResult(fallback, "fallback", ["Value is None, using fallback"])
        return DateParseResult(
            datetime.now(UTC),
            "now",
            ["Value is None and no fallback provided, using current time"],
        )

    # Handle datetime objects
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return DateParseResult(dt, "datetime", warnings)

    # Handle date-like objects (has year, month, day)
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        try:
            dt = datetime(value.year, value.month, value.day, tzinfo=UTC)  # type: ignore[attr-defined]
            return DateParseResult(dt, "date-like", warnings)
        except (TypeError, ValueError) as exc:
            warnings.append(f"Failed to construct datetime from date-like object: {exc}")

    # Convert to string for parsing
    text = str(value).strip()
    if not text:
        if fallback is not None:
            return DateParseResult(fallback, "fallback", ["Empty string, using fallback"])
        return DateParseResult(
            datetime.now(UTC),
            "now",
            ["Empty string and no fallback, using current time"],
        )

    # Try parsing strategies in order of likelihood
    parsers = [
        _parse_iso_date,
        _parse_yyyy_mm_dd,
        _parse_common_text_date,
        _parse_slashed_date,
    ]

    for parser in parsers:
        try:
            dt, format_used = parser(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
                warnings.append(f"No timezone in '{text}', assuming UTC")
            return DateParseResult(dt, format_used, warnings)
        except ValueError:
            continue

    # All parsers failed
    if fallback is not None:
        warnings.append(f"Failed to parse date '{text}', using fallback")
        return DateParseResult(fallback, "fallback", warnings)

    warnings.append(f"Failed to parse date '{text}' and no fallback, using current time")
    return DateParseResult(datetime.now(UTC), "now", warnings)


_MONTH_PART = r"(1[0-2]|0?[1-9])"
_DAY_PART = r"(3[01]|[12]\d|0?[1-9])"

_PATH_DATE_PATTERN = re.compile(
    rf"(?P<year>(?:19|20)\d{{2}})[-_/](?P<month>{_MONTH_PART})[-_/](?P<day>{_DAY_PART})",
)
_COMPACT_DATE_PATTERN = re.compile(
    r"(?P<year>(?:19|20)\d{2})(?P<month>0[1-9]|1[0-2])(?P<day>0[1-9]|[12]\d|3[01])",
)
_BODY_LABEL_PATTERN = re.compile(
    r"^\s*(date|published|created(?:_at)?)\s*[:\-]\s*(.+)$",
    re.IGNORECASE,
)


def infer_created_at_from_context(
    *,
    source_path: Path | None,
    body: str,
    max_body_lines: int = 12,
) -> tuple[datetime | None, str | None]:
    """Best-effort created_at inference from filenames, directories, or body text."""
    if source_path:
        inferred, reason = _infer_date_from_path(source_path)
        if inferred:
            return inferred, reason

    inferred, reason = _infer_date_from_body(body, max_body_lines=max_body_lines)
    if inferred:
        return inferred, reason

    return None, None


def _infer_date_from_path(path: Path) -> tuple[datetime | None, str | None]:
    candidate = _match_date_in_text(path.stem)
    if candidate:
        return candidate, f"filename '{path.name}'"

    candidate = _match_date_in_text(path.name)
    if candidate:
        return candidate, f"filename '{path.name}'"

    candidate = _match_date_in_text(str(path))
    if candidate:
        return candidate, f"path '{path}'"

    parts = path.parts
    for idx in range(len(parts) - 2):
        year_part, month_part, day_part = parts[idx : idx + 3]
        if (
            _looks_like_year(year_part)
            and _looks_like_month(month_part)
            and _looks_like_day(day_part)
        ):
            try:
                dt = datetime(int(year_part), int(month_part), int(day_part), tzinfo=UTC)
                return dt, f"directory components '{year_part}/{month_part}/{day_part}'"
            except ValueError:
                continue

    return None, None


def _infer_date_from_body(body: str, *, max_body_lines: int) -> tuple[datetime | None, str | None]:
    if not body:
        return None, None

    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        return None, None

    search_space = lines[:max_body_lines]
    for line in search_space:
        label_match = _BODY_LABEL_PATTERN.match(line)
        if not label_match:
            continue
        candidate_text = label_match.group(2).strip()
        parsed = _try_parse_candidate(candidate_text)
        if parsed:
            return parsed, f"body line '{line}'"

    for line in search_space:
        parsed = _match_date_in_text(line)
        if parsed:
            return parsed, "body text"

    return None, None


def _match_date_in_text(text: str) -> datetime | None:
    match = _PATH_DATE_PATTERN.search(text)
    if match:
        iso = _build_iso(match.group("year"), match.group("month"), match.group("day"))
        parsed = _try_parse_candidate(iso)
        if parsed:
            return parsed

    match = _COMPACT_DATE_PATTERN.search(text)
    if match:
        iso = _build_iso(match.group("year"), match.group("month"), match.group("day"))
        parsed = _try_parse_candidate(iso)
        if parsed:
            return parsed

    return None


def _looks_like_year(value: str) -> bool:
    return value.isdigit() and len(value) == 4 and 1900 <= int(value) <= 2100


def _looks_like_month(value: str) -> bool:
    return value.isdigit() and 1 <= int(value) <= 12


def _looks_like_day(value: str) -> bool:
    return value.isdigit() and 1 <= int(value) <= 31


def _try_parse_candidate(text: str) -> datetime | None:
    result = parse_date_tolerant(text, fallback=None)
    if result.format_used in {"now", "fallback"}:
        return None
    return result.dt


def _build_iso(year: str, month: str, day: str) -> str:
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _parse_iso_date(text: str) -> tuple[datetime, str]:
    """Parse ISO 8601 format: 2025-01-05T10:30:00Z or 2025-01-05."""
    normalized = text.replace("Z", "+00:00") if text.endswith("Z") else text
    dt = datetime.fromisoformat(normalized)
    return dt, "iso8601"


def _parse_yyyy_mm_dd(text: str) -> tuple[datetime, str]:
    """Parse yyyy-mm-dd with optional single-digit month/day."""
    # Handle variations like "2024-1-5" or "2024-01-5"
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", text)
    if match:
        year, month, day = match.groups()
        dt = datetime(int(year), int(month), int(day), tzinfo=UTC)
        return dt, "yyyy-mm-dd"
    msg = "Not yyyy-mm-dd format"
    raise ValueError(msg)


def _parse_common_text_date(text: str) -> tuple[datetime, str]:
    """Parse common text formats: Jan 5, 2024 or January 5, 2024 or 5 Jan 2024."""
    # Try "Month Day, Year" format
    formats = [
        (r"^(\w+)\s+(\d{1,2}),?\s+(\d{4})$", "%B %d %Y"),  # January 5, 2024
        (r"^(\w+)\s+(\d{1,2}),?\s+(\d{4})$", "%b %d %Y"),  # Jan 5, 2024
        (r"^(\d{1,2})\s+(\w+),?\s+(\d{4})$", "%d %B %Y"),  # 5 January, 2024
        (r"^(\d{1,2})\s+(\w+),?\s+(\d{4})$", "%d %b %Y"),  # 5 Jan, 2024
    ]

    for pattern, fmt in formats:
        if re.match(pattern, text):
            try:
                dt = datetime.strptime(text.replace(",", ""), fmt)
                return dt.replace(tzinfo=UTC), "text"
            except ValueError:
                continue

    msg = "Not a common text date format"
    raise ValueError(msg)


def _parse_slashed_date(text: str) -> tuple[datetime, str]:
    """Parse slashed formats: 1/5/2024 or 01/05/2024 (assumes MM/DD/YYYY)."""
    match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", text)
    if match:
        month, day, year = match.groups()
        dt = datetime(int(year), int(month), int(day), tzinfo=UTC)
        return dt, "slashed"
    msg = "Not slashed date format"
    raise ValueError(msg)
