from __future__ import annotations

from aijournal.utils.coercion import coerce_float, coerce_int


def test_coerce_float_handles_invalid_values() -> None:
    assert coerce_float("1.5") == 1.5
    assert coerce_float(None) is None
    assert coerce_float("not-a-number") is None


def test_coerce_int_handles_invalid_values() -> None:
    assert coerce_int("7") == 7
    assert coerce_int(None) is None
    assert coerce_int(3.9) == 3
    assert coerce_int("oops") is None
