# aijournal Improvements Backlog

This document captures follow-up items surfaced while testing the capture-first workflow on October 28, 2025. Notes below were reviewed and updated on October 30, 2025 to flag completed work and reprioritize the remaining backlog.

---

## 1. Configurable Ollama Host via `config.yaml` — ✅ Completed

**Status:** Implemented. `build_ollama_config_from_mapping` already respects a `host` value in `config.yaml`; precedence is CLI override → env (`AIJOURNAL_OLLAMA_HOST` / `OLLAMA_BASE_URL`) → config `host` → default `http://127.0.0.1:11434`. README/ARCHITECTURE describe the knob. No further action required.

---

## 2. Automatic Resume / Completion Checker for Capture — 🔄 Still relevant

**Pain:** When an LLM stage fails (see characterize schema errors), recovery is manual: operators must trawl `derived/logs/capture/<run_id>.jsonl` and rerun individual `ops` commands. There’s no high-level view of “what still needs to be done.”

**Proposal:** Provide a CLI helper that auto-detects incomplete derivations and optionally replays them. Two complementary pieces:

1. **Detection mode** – Inspect filesystem state to compute which stages are missing for each date (e.g., normalized entry exists but `derived/microfacts/<date>.yaml` missing or older than source hash; pending batches left unapplied; persona/index stale). Output a checklist grouped by date/stage.
2. **Replay mode** – When requested, execute the missing stages in dependency order (effectively a scoped rerun of capture limited to the detected gaps).

**Acceptance criteria**

- New command (e.g., `aijournal ops pipeline resume --date YYYY-MM-DD` or `--run capture-20251028160954`) that either prints pending steps (`--detect-only`) or executes them (`--apply`).
- Detection logic relies on artifact mtimes/hashes—no new persistent state files. Example rules:
  - If a normalized entry is newer than `derived/summaries/<date>.yaml`, flag summarize.
  - If `derived/pending/profile_updates/*.yaml` exists and hasn’t been applied, flag review.
  - If persona/index artifacts are older than their inputs, mark as stale.
- Replay mode reuses existing command runners (no duplicated business logic) and records results back into capture telemetry.

**Status:** Still outstanding and valuable. Recent refactors made stage outputs more deterministic, so the detection logic can lean on artifact hashes/mtimes without additional state. Worth scheduling once the prompt/runner work in `REVIEW.md` is underway.

**Implementation sketch**

- Build helpers to compare `data/normalized/`, `derived/summaries/`, `derived/microfacts/`, etc., using stored `source_hash` / `manifest_hashes` when available.
- CLI entry with flags: `--detect-only`, `--apply`, `--date`, `--run-id`.
- Update docs/workflow to mention the resume command under troubleshooting.

---

## 3. Structured Failure Reporting (Optional Follow-up) — 🎯 Next after resume

Detection via filesystem is nice, but we can also enrich the existing telemetry:

- Extend `capture-<id>.result.json` with a `failed_stages` array containing stage name, date, and error message.
- Surface recent failed stages in `aijournal status`, nudging operators to run the resume helper.

This builds on items 1 & 2 and pairs nicely with the resume helper. Once the detection CLI exists, wiring failure summaries into telemetry/status becomes straightforward. Keep in the backlog.

**Additional context:** The capture orchestrator today writes two files per run—`capture-<id>.jsonl` (per-event logs) and `capture-<id>.result.json` (summary). Neither explicitly lists failed stages; the CLI only prints warnings inline. By serializing failures, downstream tooling (CLI, UI, status command) can quickly surface “characterize failed for 2025-10-28” without parsing NDJSON. The resume helper would consume the same data.

---

## 4. Workspace Directory Consolidation — ✅ Completed

**Status:** Implemented as self-contained workspace directory with config at root.

**Final Design:**
- The workspace is a single directory containing `config.yaml` at its root
- All data directories (`data/`, `derived/`, `profile/`, `prompts/`) live directly under the workspace
- No nesting - the workspace **is** the root for all aijournal data
- Config location defines the workspace (no separate `workspace_root` field needed)

**Implementation Summary:**
- `WorkspacePaths` singleton manages paths within workspace directory
- `config.yaml` moved from `config/config.yaml` to workspace root `config.yaml`
- `_get_workspace()` CLI helper reads `AIJOURNAL_WORKSPACE` env var (defaults to cwd)
- `_load_config(workspace: Path)` reads from `workspace/config.yaml`
- `create_run_context(workspace: Path)` configures WorkspacePaths
- All 18 command files updated to use `workspace=` parameter
- All 207 tests passing

**Usage:**
```bash
# Initialize workspace at any location
aijournal init --path ~/my_journal

# Use via environment variable
export AIJOURNAL_WORKSPACE=~/my_journal
aijournal capture "Today's entry"

# Or run from inside workspace
cd ~/my_journal
aijournal capture "Today's entry"
```

**Structure:**
```
my_journal/           ← The workspace (any name, any location)
├── config.yaml       ← Config at workspace root
├── data/
│   ├── journal/
│   ├── normalized/
│   └── raw/
├── derived/
│   ├── summaries/
│   ├── index/
│   └── persona/
├── profile/
│   ├── self_profile.yaml
│   └── claims.yaml
└── prompts/
```

**Migration:** No backward compatibility - clean break. Users must reorganize or reinitialize.

**Key Benefits:**
- ✅ Single self-contained directory - easy to backup, move, or version control
- ✅ Config location defines workspace - no ambiguity
- ✅ Simple mental model - "workspace contains everything"
- ✅ Environment variable support - `AIJOURNAL_WORKSPACE` for scripting
- ✅ No legacy code - clean, maintainable implementation

**Post-PR Improvements (2025-11-01):**
Following PR review, added two quality-of-life improvements:
1. **Workspace Validation**: `_get_workspace()` now validates that `config.yaml` exists and provides clear error message directing users to run `aijournal init` if missing
2. **Configurable LLM Settings**: Added `LLMConfig` model with configurable `retries` (default: 4) and `timeout` (default: 120.0) settings in `config.yaml`, replacing hardcoded `DEFAULT_LLM_RETRIES` constant

All 207 tests passing, documentation updated in README.md, ARCHITECTURE.md.

---

## 5. Capture Refactor Follow-Up (Six Commit Sequence)

**Absolutely critical process reminder:** *Each* to-do below must be completed in its own commit, and the test suite **must** be green before and after every commit. No exceptions. Call this out in PR notes so the next agent doesn’t forget.

### To-Do A — Create `capture/` package scaffold
- Move stage-orchestration helpers out of `services/capture.py` into `src/aijournal/services/capture/__init__.py` (the orchestrator) plus `capture/stages/` submodules (`stage0_persist.py`, etc.).
- Keep the orchestrator thin: import stage functions and pass the typed output objects around.
- Update imports/tests accordingly. Commit once tests pass.
- ✅ 2025-10-28: Split `capture.py` into `capture/__init__.py` plus nine `capture/stages/stage*_*.py` modules; orchestrator now imports stage helpers and all tests pass (`uv run pytest`).

### To-Do B — Shared helper module (`capture/utils.py`)
- **Goal:** centralize all pure helper functions that multiple stages touch so each stage module stays focused on orchestration and typed outputs.
- **Execution steps:**
  1. Inventory helpers currently duplicated across `capture/__init__.py` and the stage modules (manifest path builders, YAML/JSON write helpers, relative-path formatting, ingest fallbacks, etc.).
  2. Create `src/aijournal/services/capture/utils.py` with cohesive, well-named functions grouped by purpose (filesystem, manifest, ingest, telemetry adapters) and document expected inputs/outputs.
  3. Update each stage module and the orchestrator to import from `capture.utils` instead of re-defining helpers; ensure imports remain acyclic.
  4. Remove the old helper definitions from their original locations to prevent drift.
- **Definition of done:** `capture/utils.py` houses the shared helpers, no stage redefines them, `uv run pytest` passes, and this note is updated with a completion date.
- ✅ 2025-10-28: Helpers moved into `capture/utils.py`, orchestrator/stages import aliases wired, and `uv run pytest` succeeded post-move.

### To-Do C — Normalize skipped-stage handling
- Introduce a helper (e.g., `record_skipped_stage(state, stage_id, name, reason)`) that wraps the noop result + duration bookkeeping.
- Replace the repeated skip branches across stages with the helper for consistency.
- Tests must stay green → commit.
- ✅ 2025-10-28: Added `record_stage_outcome`/`record_skipped_stage` closures inside `run_capture`, rewired every stage skip branch to use them, and kept `uv run pytest` green.

### To-Do D — Centralize telemetry emission
- Create a small telemetry helper (`emit_stage_event(log_event, stage_name, op_result)`) in `capture/utils.py` (or similar) and use it for index/persona/pack done events.
- Remove direct `log_event` payload duplication in stage code.
- Verify tests → commit.
- ✅ 2025-10-28: Added `_emit_operation_event` helper and routed index/persona telemetry through it; pack relies on stage recording only. Tests stay green via `uv run pytest`.

### To-Do E — Focused stage tests
- Add unit-level tests per stage helper (mocking external commands) so `tests/services/test_capture.py` is no longer the only coverage point.
- Ensure new tests live under `tests/services/capture/` with clear naming.
- Run full test suite → commit.
- ✅ 2025-10-28: Added `test_stage_summarize` (success/failure) and `test_stage_persona` (build/no-op) with command stubs and green `uv run pytest`.

### To-Do F — Trim `CaptureState`
- After helpers return typed outputs, remove redundant fields from `CaptureState` (e.g., `index_rebuilt_flag`, `changed_dates` if no longer needed) and pass data through return values instead.
- Re-run tests, confirm orchestrator still composes, and **commit**.
- ✅ 2025-10-28: Deleted the unused `CaptureState` dataclass and associated imports; `run_capture` now runs on local state only. `uv run pytest` remains green.

> Remember: six commits, tests green every time. Document progress in this file (append short notes under each bullet) as tasks are completed.

---

## 6. Structured Logging & Command Standardization — 🆕 Planned

**Goal:** Make every command easier to debug (especially with AI assistance) and enforce a consistent orchestration structure without adding new behaviour.

### 6.1 Structured Logging Scaffold
- Introduce a lightweight `RunContext` object that carries `root`, `config`, fake/live flag, and a new `StructuredLogger`.
- Logger writes NDJSON records to `derived/logs/run_trace.jsonl` with fields like `step`, `command`, `duration_ms`, `inputs_summary`, `output_path`, `llm_attempts`, `coercions`, `error`.
- Add CLI flags (`--trace` / `--verbose-json`) to mirror log entries to stdout for manual debugging.
- Provide `aijournal ops logs tail --last N` helper to pretty-print recent trace entries.
- Acceptance: `advise` command uses the logger end-to-end, tests assert log file creation, documentation updated.
- ✅ 2025-10-30: Implemented shared `RunContext`/`StructuredLogger`, added CLI trace flags and the `ops logs tail` helper, refreshed docs/tests.

### 6.2 Standard Command Skeleton
- For each command module define three top-level functions: `prepare_inputs(ctx, options)`, `invoke_pipeline(ctx, prepared)`, `persist_output(ctx, result)`.
- `CommandOptions` becomes a small Pydantic model populated by Typer parser so orchestration code receives typed input instead of raw kwargs.
- Update one pilot command (`advise`) to this pattern, then roll out across the rest in small commits.
- Acceptance: every command module follows the same structure; imports stay acyclic; CI/tests remain green after each migration step.
- ✅ 2025-10-30: `advise`, `summarize`, and `extract-facts` now follow the standard pipeline skeleton via `run_command_pipeline`; tests are green.
- ✅ 2025-10-31: Completed rollout to remaining command surfaces including `index` and `ingest`; all CLI modules now share the standardized skeleton.

### 6.3 Rollout Strategy
1. Implement logging + context on a single command, adjust tests/docs.
2. Adopt the standardized function names on that command, verify readability gains.
3. Iterate across remaining commands in batches, updating modules & tests per commit.
4. Extend docs (ARCHITECTURE + CONTRIBUTING) with the logging format and the new orchestration pattern.
- ✅ 2025-10-30: Logging scaffold, command skeleton, docs, and tests landed; remaining commands can migrate incrementally following the same pattern.

> Reminder: treat each stage of the rollout as its own commit with `uv run pytest` + targeted tests green before moving to the next batch.
