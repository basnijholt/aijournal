# Changelog

## Unreleased

- Added `aijournal new --fake N` (with `--seed`) to synthesize deterministic Markdown entries for fixtures, demos, and CI without hitting Ollama.
- Added `aijournal index rebuild/tail` to generate Annoy + SQLite retrieval indexes (with chunk manifests + meta) using local or fake embeddings.
- Added `aijournal.services.retriever.Retriever` with ANN + fallback search plus Pytests for both modes.
- Added `aijournal persona build` to generate `derived/persona/persona_core.yaml` with configurable token budgets, claim ranking, trimming metadata, and full schema/Pytest coverage.
- Added `aijournal persona status` plus pack-level persona gating: persona core stores profile mtimes, `pack` refuses to run without it, and warns when profile edits make the cache stale.

## v0.2.0 — 2025-10-25

- Added `aijournal pack` levels **L3/L4**, including history windows, prompt/config inclusion, and smarter trimming with `meta.trimmed` details.
- Introduced profile suggestion + apply workflows and the interviewer-style advise command (fake Ollama mode).
- Implemented Ollama `health` probe plus the core CLI flows (`init`, `new`, `normalize`).
- Expanded README usage docs covering pack options, fake mode expectations, and CLI ergonomics.
