"""Tests for the public capture API models."""

from __future__ import annotations

from aijournal.api.capture import CaptureInput, CaptureRequest


def test_capture_request_has_no_stage_fields() -> None:
    """The public request schema must not expose internal stage controls."""
    fields = CaptureRequest.model_fields
    assert "min_stage" not in fields
    assert "max_stage" not in fields


def test_capture_request_to_input_conversion() -> None:
    """CaptureInput should faithfully extend CaptureRequest data."""
    request = CaptureRequest(source="stdin", text="Hello", tags=["focus"])
    capture_input = CaptureInput.from_request(request, min_stage=2, max_stage=4)

    for key, value in request.model_dump(mode="python").items():
        assert getattr(capture_input, key) == value
    assert capture_input.min_stage == 2
    assert capture_input.max_stage == 4
