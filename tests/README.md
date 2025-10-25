# Tests

`uv run pytest -q` exercises the full CLI surface plus the shared Pydantic helpers. Most test
modules export a `_has_command` guard so partially implemented commands can be skipped without
breaking the suite.

Key suites:

- `tests/test_models_io.py` — round-trip coverage for every Pydantic model. Ensures the runtime
  validation remains aligned with the Python definitions.
- `tests/test_cli_*.py` — functional coverage for init/new/ingest/normalize/summarize/facts/profile
  flows, all running with `AIJOURNAL_FAKE_OLLAMA=1` so CI never needs a model.
- `tests/test_cli_pack.py` — validates packing logic, trim ordering, and token budgeting.

When developing locally, set `AIJOURNAL_FAKE_OLLAMA=1` before running tests to avoid hitting a live
model:

```sh
export AIJOURNAL_FAKE_OLLAMA=1
uv run pytest -q
```

The CLI automatically falls back to fake fixtures if an Ollama call fails, but exporting the env var
keeps results deterministic for golden snapshots.
