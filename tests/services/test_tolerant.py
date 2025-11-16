"""Tests for tolerant front-matter and date parsing utilities."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from aijournal.services.capture.tolerant import (
    infer_created_at_from_context,
    parse_date_tolerant,
    split_frontmatter_tolerant,
)
from aijournal.services.capture.utils import split_frontmatter


class TestSplitFrontmatterTolerant:
    """Tests for split_frontmatter_tolerant function."""

    def test_yaml_frontmatter_valid(self) -> None:
        text = """---
id: test-entry
title: Test Entry
tags:
  - work
  - focus
created_at: 2025-01-05
---

This is the body content.
"""
        result = split_frontmatter_tolerant(text)
        assert result.format == "yaml"
        assert result.data["id"] == "test-entry"
        assert result.data["title"] == "Test Entry"
        assert result.data["tags"] == ["work", "focus"]
        assert result.body == "This is the body content.\n"
        assert not result.warnings

    def test_yaml_frontmatter_with_unknown_keys(self) -> None:
        text = """---
id: test
custom_field: custom_value
another_field: 123
---
Body
"""
        result = split_frontmatter_tolerant(text)
        assert result.format == "yaml"
        assert result.data["custom_field"] == "custom_value"
        assert len(result.warnings) == 1
        assert "unknown" in result.warnings[0].lower()
        assert "custom_field" in result.warnings[0]

    def test_yaml_frontmatter_with_utf8_bom(self) -> None:
        text = """\ufeff---
title: BOM Entry
created_at: 2025-01-05
---

Body content here.
"""
        result = split_frontmatter_tolerant(text)
        assert result.format == "yaml"
        assert result.data["title"] == "BOM Entry"
        assert result.body == "Body content here.\n"
        assert not result.warnings

    def test_yaml_frontmatter_with_zero_width_space_prefix(self) -> None:
        text = """\u200b---
title: Hidden Space Entry
---

Body content here.
"""
        result = split_frontmatter_tolerant(text)
        assert result.format == "yaml"
        assert result.data["title"] == "Hidden Space Entry"
        assert result.body == "Body content here.\n"
        assert not result.warnings

    def test_toml_frontmatter_valid(self) -> None:
        # Use YAML-compatible TOML syntax since we're using yaml.safe_load
        text = """+++
id: test-entry
title: Test Entry
tags:
  - work
  - focus
+++

Body content here.
"""
        result = split_frontmatter_tolerant(text)
        assert result.format == "toml"
        assert result.data["id"] == "test-entry"
        assert result.body == "Body content here.\n"

    def test_json_frontmatter_valid(self) -> None:
        text = """{
  "id": "json-entry",
  "title": "JSON Entry",
  "tags": ["json", "test"],
  "created_at": "2025-01-05T10:30:00Z"
}

Body content after JSON.
"""
        result = split_frontmatter_tolerant(text)
        assert result.format == "json"
        assert result.data["id"] == "json-entry"
        assert result.data["title"] == "JSON Entry"
        assert result.body == "Body content after JSON.\n"
        assert not result.warnings

    def test_json_frontmatter_nested_objects(self) -> None:
        text = """{
  "id": "complex",
  "metadata": {
    "author": "user",
    "version": 1
  }
}

Body
"""
        result = split_frontmatter_tolerant(text)
        assert result.format == "json"
        assert result.data["metadata"]["author"] == "user"  # type: ignore[index]

    def test_no_frontmatter(self) -> None:
        text = "Just plain markdown content without any frontmatter."
        result = split_frontmatter_tolerant(text)
        assert result.format == "none"
        assert result.data == {}
        assert result.body == text
        assert len(result.warnings) == 1
        assert "no front-matter" in result.warnings[0].lower()

    def test_empty_input(self) -> None:
        result = split_frontmatter_tolerant("")
        assert result.format == "none"
        assert result.data == {}
        assert result.body == ""
        assert len(result.warnings) == 1
        assert "empty" in result.warnings[0].lower()

    def test_incomplete_yaml_frontmatter(self) -> None:
        text = """---
id: incomplete
title: No closing delimiter

Body starts here without closing ---
"""
        result = split_frontmatter_tolerant(text)
        assert result.format == "yaml"
        assert result.data == {}
        assert result.body == text
        assert len(result.warnings) == 1
        # Will fail to parse due to YAML syntax error (body looks like key without value)
        assert "failed to parse" in result.warnings[0].lower()

    def test_malformed_yaml(self) -> None:
        text = """---
id: test
title: [unclosed bracket
tags:
  - tag1
---
Body
"""
        result = split_frontmatter_tolerant(text)
        assert result.format == "yaml"
        assert result.data == {}
        assert result.body == text
        assert len(result.warnings) == 1
        assert "failed to parse" in result.warnings[0].lower()

    def test_malformed_json(self) -> None:
        text = """{
  "id": "test",
  "title": "Missing closing brace"

Body content
"""
        result = split_frontmatter_tolerant(text)
        assert result.format == "json"
        assert result.data == {}
        assert result.body == text
        assert len(result.warnings) == 1

    def test_json_not_dict(self) -> None:
        text = """["array", "not", "dict"]

Body
"""
        result = split_frontmatter_tolerant(text)
        assert result.format == "json"
        assert result.data == {}
        assert len(result.warnings) == 1
        assert "not a dictionary" in result.warnings[0].lower()

    def test_empty_frontmatter_block(self) -> None:
        text = """---
---

Body content.
"""
        result = split_frontmatter_tolerant(text)
        assert result.format == "yaml"
        assert result.data == {}
        assert result.body == "Body content.\n"
        assert len(result.warnings) == 1
        assert "empty" in result.warnings[0].lower()

    def test_yaml_null_frontmatter(self) -> None:
        text = """---
null
---

Body
"""
        result = split_frontmatter_tolerant(text)
        assert result.format == "yaml"
        assert result.data == {}
        assert len(result.warnings) == 1

    def test_whitespace_handling(self) -> None:
        text = """

---
id: test
---

Body
"""
        result = split_frontmatter_tolerant(text)
        assert result.format == "yaml"
        assert result.data["id"] == "test"
        assert result.body.strip() == "Body"


class TestSplitFrontmatterStrict:
    """Tests for the strict split_frontmatter helper."""

    def test_yaml_frontmatter_with_utf8_bom(self) -> None:
        text = """\ufeff---
title: Strict BOM
---

Strict body line.
"""
        frontmatter, body = split_frontmatter(text)
        assert frontmatter["title"] == "Strict BOM"
        assert body == "Strict body line.\n"

    def test_yaml_frontmatter_with_zero_width_space_prefix(self) -> None:
        text = """\u200b---
title: Strict Hidden Space
---

Strict body line.
"""
        frontmatter, body = split_frontmatter(text)
        assert frontmatter["title"] == "Strict Hidden Space"
        assert body == "Strict body line.\n"


class TestParseDateTolerant:
    """Tests for parse_date_tolerant function."""

    def test_iso8601_with_timezone(self) -> None:
        result = parse_date_tolerant("2025-01-05T10:30:00Z")
        assert result.dt == datetime(2025, 1, 5, 10, 30, 0, tzinfo=UTC)
        assert result.format_used == "iso8601"
        assert not result.warnings

    def test_iso8601_with_offset(self) -> None:
        result = parse_date_tolerant("2025-01-05T10:30:00+05:30")
        assert result.format_used == "iso8601"
        assert result.dt.year == 2025
        assert result.dt.month == 1
        assert result.dt.day == 5

    def test_iso8601_date_only(self) -> None:
        result = parse_date_tolerant("2025-01-05")
        assert result.dt.year == 2025
        assert result.dt.month == 1
        assert result.dt.day == 5
        assert result.format_used == "iso8601"

    def test_yyyy_mm_dd_variations(self) -> None:
        # Single-digit month and day
        result = parse_date_tolerant("2024-1-5")
        assert result.dt == datetime(2024, 1, 5, tzinfo=UTC)
        assert result.format_used == "yyyy-mm-dd"

        # Zero-padded
        result = parse_date_tolerant("2024-01-05")
        assert result.dt == datetime(2024, 1, 5, tzinfo=UTC)

    def test_common_text_formats(self) -> None:
        # Full month name with comma
        result = parse_date_tolerant("January 5, 2024")
        assert result.dt == datetime(2024, 1, 5, tzinfo=UTC)
        assert result.format_used == "text"

        # Abbreviated month
        result = parse_date_tolerant("Jan 5, 2024")
        assert result.dt == datetime(2024, 1, 5, tzinfo=UTC)
        assert result.format_used == "text"

        # Day first
        result = parse_date_tolerant("5 January 2024")
        assert result.dt == datetime(2024, 1, 5, tzinfo=UTC)
        assert result.format_used == "text"


class TestInferCreatedAtFromContext:
    def test_infers_from_filename(self) -> None:
        dt, reason = infer_created_at_from_context(
            source_path=Path("/notes/2024-03-15-focus.md"),
            body="",
        )
        assert dt == datetime(2024, 3, 15, tzinfo=UTC)
        assert reason
        assert "filename" in reason

    def test_infers_from_directory_components(self) -> None:
        dt, reason = infer_created_at_from_context(
            source_path=Path("/blog/2023/12/25/holiday.md"),
            body="",
        )
        assert dt == datetime(2023, 12, 25, tzinfo=UTC)
        assert reason
        assert any(keyword in reason for keyword in ("directory", "path"))

    def test_infers_from_body_label(self) -> None:
        body = """
Date: Jan 2, 2022
Entry text.
"""
        dt, reason = infer_created_at_from_context(source_path=None, body=body)
        assert dt == datetime(2022, 1, 2, tzinfo=UTC)
        assert reason
        assert "body line" in reason

    def test_infers_from_body_without_label(self) -> None:
        body = "Captured reflections from 2014/07/03 during the hike."
        dt, reason = infer_created_at_from_context(source_path=None, body=body)
        assert dt == datetime(2014, 7, 3, tzinfo=UTC)
        assert reason
        assert "body text" in reason

    def test_infers_from_compact_filename(self) -> None:
        dt, reason = infer_created_at_from_context(
            source_path=Path("/notes/meeting_20190309.md"),
            body="",
        )
        assert dt == datetime(2019, 3, 9, tzinfo=UTC)
        assert reason
        assert "filename" in reason

    def test_returns_none_when_no_matches(self) -> None:
        dt, reason = infer_created_at_from_context(source_path=None, body="No hints here")
        assert dt is None
        assert reason is None

        # Abbreviated, day first
        result = parse_date_tolerant("5 Jan 2024")
        assert result.dt == datetime(2024, 1, 5, tzinfo=UTC)

    def test_slashed_format(self) -> None:
        # MM/DD/YYYY
        result = parse_date_tolerant("1/5/2024")
        assert result.dt == datetime(2024, 1, 5, tzinfo=UTC)
        assert result.format_used == "slashed"

        # Zero-padded
        result = parse_date_tolerant("01/05/2024")
        assert result.dt == datetime(2024, 1, 5, tzinfo=UTC)

    def test_datetime_object(self) -> None:
        dt = datetime(2025, 1, 5, 10, 30, tzinfo=UTC)
        result = parse_date_tolerant(dt)
        assert result.dt == dt
        assert result.format_used == "datetime"
        assert not result.warnings

    def test_datetime_without_timezone(self) -> None:
        dt = datetime(2025, 1, 5, 10, 30)
        result = parse_date_tolerant(dt)
        assert result.dt == dt.replace(tzinfo=UTC)
        assert result.format_used == "datetime"

    def test_date_object(self) -> None:
        d = date(2025, 1, 5)
        result = parse_date_tolerant(d)
        assert result.dt == datetime(2025, 1, 5, tzinfo=UTC)
        assert result.format_used == "date-like"

    def test_none_with_fallback(self) -> None:
        fallback = datetime(2024, 12, 31, tzinfo=UTC)
        result = parse_date_tolerant(None, fallback=fallback)
        assert result.dt == fallback
        assert result.format_used == "fallback"
        assert len(result.warnings) == 1
        assert "none" in result.warnings[0].lower()

    def test_none_without_fallback(self) -> None:
        result = parse_date_tolerant(None)
        assert result.format_used == "now"
        assert len(result.warnings) == 1
        # Should be very close to current time
        assert (datetime.now(UTC) - result.dt).total_seconds() < 1

    def test_empty_string_with_fallback(self) -> None:
        fallback = datetime(2024, 1, 1, tzinfo=UTC)
        result = parse_date_tolerant("", fallback=fallback)
        assert result.dt == fallback
        assert result.format_used == "fallback"

    def test_unparseable_with_fallback(self) -> None:
        fallback = datetime(2024, 6, 15, tzinfo=UTC)
        result = parse_date_tolerant("not a date at all", fallback=fallback)
        assert result.dt == fallback
        assert result.format_used == "fallback"
        assert len(result.warnings) == 1
        assert "failed to parse" in result.warnings[0].lower()

    def test_unparseable_without_fallback(self) -> None:
        result = parse_date_tolerant("invalid date string")
        assert result.format_used == "now"
        assert len(result.warnings) == 1

    def test_timezone_missing_warning(self) -> None:
        # ISO date without timezone should add warning
        result = parse_date_tolerant("2025-01-05T10:30:00")
        assert result.dt.tzinfo == UTC
        assert len(result.warnings) == 1
        assert "timezone" in result.warnings[0].lower()
        assert "utc" in result.warnings[0].lower()

    def test_various_string_types(self) -> None:
        # Test that we handle str() conversion properly
        result = parse_date_tolerant(2025)  # Not a valid date, but shouldn't crash
        assert result.format_used in ("fallback", "now")

    def test_whitespace_handling(self) -> None:
        result = parse_date_tolerant("  2025-01-05  ")
        assert result.dt == datetime(2025, 1, 5, tzinfo=UTC)
        assert result.format_used == "iso8601"


class TestIntegrationScenarios:
    """Integration tests for combined usage patterns."""

    def test_capture_with_multiple_date_formats(self) -> None:
        """Test that various date formats in front-matter are handled."""
        texts = [
            ('---\ncreated_at: "2025-01-05T10:30:00Z"\n---\nBody', "iso8601"),
            ('---\ncreated_at: "Jan 5, 2025"\n---\nBody', "text"),
            ('---\ncreated_at: "2025-1-5"\n---\nBody', "yyyy-mm-dd"),
        ]

        for text, _expected_format in texts:
            fm_result = split_frontmatter_tolerant(text)
            created_at = fm_result.data.get("created_at")
            date_result = parse_date_tolerant(created_at)
            assert date_result.dt.year == 2025
            assert date_result.dt.month == 1
            assert date_result.dt.day == 5

    def test_malformed_entry_never_crashes(self) -> None:
        """Ensure that even completely malformed input is handled gracefully."""
        malformed_inputs = [
            "",  # Empty
            "No frontmatter at all",  # No delimiter
            "---\nbroken: [yaml\n---\nBody",  # Malformed YAML
            '{"broken": json}\nBody',  # Malformed JSON
            "---\n---\nEmpty frontmatter",  # Empty block
            "---\ncreated_at: not a date\n---\nBody",  # Invalid date
        ]

        for text in malformed_inputs:
            fm_result = split_frontmatter_tolerant(text)
            # Should always return a result, never raise
            assert isinstance(fm_result.data, dict)
            assert isinstance(fm_result.body, str)
            assert isinstance(fm_result.warnings, list)

            # Try parsing any created_at field
            if "created_at" in fm_result.data:
                date_result = parse_date_tolerant(
                    fm_result.data["created_at"],
                    fallback=datetime(2025, 1, 1, tzinfo=UTC),
                )
                assert isinstance(date_result.dt, datetime)
                assert isinstance(date_result.warnings, list)
