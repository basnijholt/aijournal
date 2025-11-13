"""Behavioral tests for YAML serialization helpers."""

from __future__ import annotations

from aijournal.io.yaml_io import dump_yaml


def test_dump_yaml_preserves_unicode_characters() -> None:
    payload = {"title": "Karakter – F. Bordewijk 📖"}

    serialized = dump_yaml(payload)

    assert "Karakter – F. Bordewijk 📖" in serialized
    assert "\\u" not in serialized


def test_dump_yaml_uses_literal_block_for_multiline_strings() -> None:
    payload = {"summary": "*1938*\n\n> Op dat ogenblik"}

    serialized = dump_yaml(payload)

    assert "summary: |" in serialized
    assert "*1938*" in serialized.splitlines()[1]
    assert "  > Op dat ogenblik" in serialized
