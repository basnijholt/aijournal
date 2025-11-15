# Changelog

## Unreleased

- **Workspace validation**: Commands now validate that the workspace directory contains `config.yaml` and provide helpful error messages directing users to run `aijournal init` if missing.
- **Configurable LLM settings**: Added `llm` section to `config.yaml` with configurable `retries` (default: 4) and `timeout` (default: 120.0) settings, replacing hardcoded retry constants.
- Chat service fully migrated to strict domain models: `ChatService` now returns `ChatTurn`/`ChatTelemetry` `StrictModel`s end-to-end and CLI/API/tests no longer depend on legacy dataclasses.
- Capture stages 3–4 now require day-level summaries from stage 2; `extract-facts`
  and `ops profile update` load the summary JSON, feed it to their prompts, and
  fail fast with a remediation hint when `derived/summaries/<date>.yaml` is
  missing.
- Advice cards plus chat summaries/learnings now persist as `Artifact[T]` envelopes with dedicated domain schemas and updated fixtures/tests.
- Added `aijournal ops audit provenance [--fix]` to report or redact any persisted `span.text` provenance and wired it into docs/workflow guidance.
- Removed the legacy pending-batch YAML readers; CLI/modules now require strict `Artifact[ProfileUpdateBatch]` envelopes and surface guidance when stale files are discovered.
- CLI commands `summarize`, `facts`, `profile update`, and `advise` now run through the shared Pydantic AI agent pipeline (`run_ollama_agent` + structured response models) and surface errors when schemas fail validation instead of emitting heuristic fallbacks.
- Centralized float/int coercion in `aijournal.utils.coercion` and extended the chat service to respect `config.chat` overrides (model, temperature, seed, timeout).
- Expanded README/plan docs and tests to describe and exercise the unified Pydantic AI configuration helper.
- Added shared `--progress` and `--retries` flags across long-running LLM calls to surface per-entry progress and control retry behaviour.
- Added `aijournal new --fake N` (with `--seed`) to synthesize deterministic Markdown entries for fixtures, demos, and CI without hitting Ollama.
- Added `aijournal index rebuild/tail` to generate a Chroma-backed retrieval index (with chunk artifacts + meta) using local or fake embeddings.
- Added `aijournal.services.retriever.Retriever` with ANN + fallback search plus Pytests for both modes.
- Added `aijournal persona build` to generate `derived/persona/persona_core.yaml` with configurable token budgets, claim ranking, trimming metadata, and full schema/Pytest coverage.
- Added `aijournal persona status` plus pack-level persona gating: persona core stores profile mtimes, `pack` refuses to run without it, and warns when profile edits make the cache stale.
- Chat loop now persists transcripts/summaries/learnings under `derived/chat_sessions/<session>/`, emits JSON telemetry, and supports `--session`, `--save/--no-save`, and `--feedback up|down` to nudge cited claim strengths while queuing feedback batches for review.
- Introduced `aijournal chatd` (FastAPI) which streams NDJSON responses, mirrors the CLI orchestrator, and reuses the transcript/feedback plumbing; accompanying tests exercise the API in fake mode.
- Reworked `prompts/profile_update.md` and live-mode handlers so structured responses validate cleanly; added coverage that patches the structured runner. `aijournal interview` now calls a new `prompts/interview.md` workflow in live mode while respecting `coaching_prefs.probing` limits.
- Added Python telemetry hooks for `aijournal pack` (token budgets) and chat (retrieval latency) so automation can tail structured logs.
- Interview ranking now applies information-gain heuristics (staleness, strength, scope gaps, pending prompts) with richer metadata for prompts; both the CLI fallback and live LLM paths (and Advisor Mode) now consume these hints for context-aware follow-ups.

## v0.2.0 — 2025-10-25

- Added `aijournal pack` levels **L3/L4**, including history windows, prompt/config inclusion, and smarter trimming with `meta.trimmed` details.
- Introduced profile suggestion + apply workflows and the interviewer-style advise command (fake Ollama mode).
- Implemented Ollama `health` probe plus the core CLI flows (`init`, `new`, `normalize`).
- Expanded README usage docs covering pack options, fake mode expectations, and CLI ergonomics.
