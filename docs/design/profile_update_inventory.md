# Profile Update Surface Inventory

_Updated: 2025-11-14_

> Status: Prompt3 cleanup is complete. The list below is preserved for historical/audit context so future agents know which legacy surfaces were removed or renamed when `profile_update` replaced the old flows.

This note originally captured every code surface that referenced the legacy
`profile_suggest` / `characterize` flows. Capture now routes exclusively
through the unified `profile_update` stage.

## Command / CLI entry points

- `src/aijournal/cli.py`
  - `profile.suggest` command → `run_profile_suggest`
  - `profile.apply` consumers expect artifacts from stage 4
  - `characterize` command and `ops pipeline characterize` wrapper
- `src/aijournal/commands/profile.py`
  - Houses `run_profile_suggest`, apply/status plumbing, summarizer helpers,
    and derived artifact writers.
- `src/aijournal/commands/characterize.py`
  - Orchestrates LLM calls for stage 5, produces
    `derived/pending/profile_updates/*.yaml` batches.

## Capture pipeline integration

- ✅ `src/aijournal/services/capture/__init__.py` now invokes the unified
  `derive.profile_update` stage (wrapping `stage4_profile_update.py`).
- ✅ `src/aijournal/services/capture/stages/stage4_profile_update.py` replaces
  the old stage4/stage5 pair.
- `src/aijournal/services/capture/graceful.py` still exposes the legacy
  wrappers, though capture no longer consumes them.

## Prompt + DTO definitions

- `prompts/profile_suggest.md` and `prompts/characterize.md` define the old
  LLM contracts.
- `prompts/examples/profile_suggest.json` and `prompts/examples/characterize.json`
  back the example validation tests.
- `src/aijournal/domain/prompts.py`
  - DTO containers (`PromptProfileUpdates`, `PromptClaimItem`, etc.) used by
    both prompts, plus converters into `ProfileUpdateProposals`.

## Pipeline helpers

- `src/aijournal/pipelines/characterize.py`
  - Shared normalization and fake-mode logic for stage 5.
- `src/aijournal/pipelines/facts.py`
  - Provides normalization helpers that stage 4 and 5 import when converting
    LLM-emitted claim DTOs.

## Tests and fixtures

- `tests/prompts/test_prompt_examples.py` exercises
  `profile_suggest.json` + `characterize.json` payloads.
- `tests/cli/test_cli_profile_suggest.py`, `tests/test_cli_characterize.py`,
  and capture-service tests (`tests/services/test_capture.py`,
  `tests/services/capture/test_stage_profile.py`, etc.) assert both stages run.
- Simulator validators (`tests/simulator/validators.py`) expect stage 4 and 5
  artifacts when replaying capture runs.

This inventory will guide the remaining Prompt 3 workstreams:

1. Introduce the new unified prompt (`prompts/profile_update.md`) and pipeline.
2. Wire a single `profile_update` stage into capture + CLI.
3. Delete/retire every surface listed above once parity tests pass.
