# aijournal Improvements Backlog

This document captures follow-up items surfaced while testing the capture-first workflow on October 28, 2025. Each item includes a short rationale, acceptance criteria, and an implementation sketch so we can schedule/elaborate later.

---

## 1. Configurable Ollama Host via `config/config.yaml`

**Pain:** Capture obeys `AIJOURNAL_MODEL` from the config file, but the host still requires exporting `AIJOURNAL_OLLAMA_HOST`. On live runs we *always* point at `http://192.168.1.143:11434`, so missing the export leads to flaky structured-output failures (characterize blew up under the default `localhost`).

**Proposal:** Allow `config/config.yaml` to carry a `host` field. When set, `build_ollama_config_from_mapping` should treat it as the default, with environment variables retaining higher priority for ad-hoc overrides.

**Acceptance criteria**

- Add optional `host` key to the config schema (documented in README + sample config).
- `build_ollama_config_from_mapping` resolves host precedence as: CLI override > env (`AIJOURNAL_OLLAMA_HOST` or `OLLAMA_BASE_URL`) > config `host` > default `http://127.0.0.1:11434`.
- `aijournal ops system doctor` and capture telemetry display the resolved host so misconfigurations are obvious.

**Implementation sketch**

- Update config schema (and `schema.py` models) to accept `host`.
- Extend `build_ollama_config_from_mapping` with a `settings.get("host")` fallback.
- Adjust docs (README, ARCHITECTURE) to show the new knob and precedence rules.

---

## 2. Automatic Resume / Completion Checker for Capture

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

**Implementation sketch**

- Build helpers to compare `data/normalized/`, `derived/summaries/`, `derived/microfacts/`, etc., using stored `source_hash` / `manifest_hashes` when available.
- CLI entry with flags: `--detect-only`, `--apply`, `--date`, `--run-id`.
- Update docs/workflow to mention the resume command under troubleshooting.

---

## 3. Structured Failure Reporting (Optional Follow-up)

Detection via filesystem is nice, but we can also enrich the existing telemetry:

- Extend `capture-<id>.result.json` with a `failed_stages` array containing stage name, date, and error message.
- Surface recent failed stages in `aijournal status`, nudging operators to run the resume helper.

This builds on items 1 & 2 but isn’t strictly required once automatic detection/resume is available.

**Additional context:** The capture orchestrator today writes two files per run—`capture-<id>.jsonl` (per-event logs) and `capture-<id>.result.json` (summary). Neither explicitly lists failed stages; the CLI only prints warnings inline. By serializing failures, downstream tooling (CLI, UI, status command) can quickly surface “characterize failed for 2025-10-28” without parsing NDJSON. The resume helper would consume the same data.

---

## 4. Workspace Directory Consolidation

**Pain:** The repo root currently includes multiple top-level data directories (`data/`, `derived/`, `profile/`, `config/`, `prompts/`, `logs/`, etc.). For new operators this feels noisy, and automated tooling (e.g., backups) must exclude lots of sibling paths. We want a single “workspace” folder that encapsulates all mutable artifacts and keeps the repository’s root tidy.

**Proposal:** Introduce a `workspace/` (name TBD) directory that contains all runtime/stateful folders, while the root keeps only code and documentation. Existing commands should honor the new layout via a configurable base path so migrations are straightforward.

**Acceptance criteria**

- Add a config option (e.g., `paths.workspace_root`) defaulting to `workspace/`. Under this directory, materialize the existing `data/`, `derived/`, `profile/`, `prompts/`, `config/`, `logs/`, etc.
- Update path helpers (`utils/paths.py`, normalization, pipelines, capture) to resolve everything relative to the workspace root.
- Provide a migration command or script that moves current folders into `workspace/` without data loss (skip if already nested).
- Documentation updates (README, ARCHITECTURE, workflow) to reference the new hierarchy and note that old layouts remain supported for a release via backward-compat lookup.
- Ensure `.gitignore`, tests, and fake fixtures respect the new structure.

**Implementation sketch**

- Define `WorkspacePaths` helper exposing `workspace_root`, `data_dir`, `derived_dir`, etc., derived from config/env.
- Adjust `aijournal init` to create `workspace/` (or the configured root) and lay out subdirectories there.
- Capture/backfill: detect legacy layout (existing `/data/` at root) and either (a) continue using it via compatibility mode, or (b) prompt the user / run migration.
- Update CLI commands/tests to use the helper instead of hard-coded `Path("data")` references.
