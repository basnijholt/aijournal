from __future__ import annotations

import pytest

from aijournal.schema import SchemaValidationError, validate_schema


def test_validate_schema_raises_with_aggregate_errors() -> None:
    payload = {"unexpected": True}

    with pytest.raises(SchemaValidationError) as excinfo:
        validate_schema("summary", payload)

    err = excinfo.value
    assert err.schema == "summary"
    assert err.errors
    assert "Field required" in err.errors[0]
    assert "Schema 'summary' validation failed" in str(err)
