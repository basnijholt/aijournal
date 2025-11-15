# Unified `profile_update` Pipeline

_Last updated: 2025-11-14_

## Goals

- Replace the overlapping **Stage 4 `profile_suggest`** and **Stage 5 `characterize`** passes with a **single authoritative `profile_update` stage** per `docs/archive/2025-11-15_prompt3.md`.
- Feed the LLM richer context (normalized entries, daily summaries, microfacts, recent history, current persona) so it can emit claims, facet updates, and interview prompts in one shot.
- Keep the **existing persistence layer** (`ProfileUpdateProposals`, `ProfileUpdateBatch`, `profile/claims.yaml`, `profile/self_profile.yaml`) and **review/apply semantics** intact while deleting legacy entry points.

## What We Keep

- **Domain/DTOs**: `PromptProfileUpdates`, `ClaimProposal`, `FacetChange`, `ProfileUpdateProposals`, `ProfileUpdateBatch`, `ProfileUpdatePreview`.
- **Artifact format**: `derived/pending/profile_updates/<date>-<timestamp>.yaml` with `ArtifactKind.PROFILE_UPDATES`.
- **Apply plumbing**: `apply_claim_upsert`, `apply_profile_update`, `apply_profile_update_batch`, and `aijournal profile apply` CLI semantics (auto-apply still gated by `--apply-profile` during capture).
- **Fake-mode hooks**: keep deterministic generators so CI and offline runs stay reproducible.

## What We Remove

- `prompts/profile_suggest.md`, `prompts/characterize.md`, and any code paths that still load them.
- `derived/profile_proposals/` artifacts and the Stage 4 helper `run_profile_stage_4` that writes them.
- CLI surfaces: `aijournal ops profile suggest`, `aijournal ops pipeline characterize`, `aijournal ops pipeline review` (since `profile apply` already handles batch review/apply).
- Stage-5 specific helpers that only exist to juggle characterize batches once the unified stage lands.

## New Components

### Prompt Contract (`prompts/profile_update.md`)

A single prompt instructing the "Profile Update Agent" to:

1. Read inputs: normalized entries for the date, `SUMMARY_JSON` (Stage 2 output), `MICROFACTS_JSON` (Stage 3 output), current profile/claims, optional `RECENT_SUMMARIES_JSON` / `RECENT_MICROFACTS_JSON`, and optional manifest metadata.
2. Emit output JSON with exactly `claims`, `facets`, `interview_prompts`, using the *existing* `PromptProfileUpdates` schema (strings for interview prompts, 8-field claim DTO, facet DTO with `operation ∈ {set, remove}`).
3. Provide ≤25-word rationales and cite `evidence_entry` / `evidence_para` for each proposal; refuse speculative updates.

### Pipeline & DTO Aggregation

Add `src/aijournal/pipelines/profile_update.py` that:

1. Loads inputs
   - Normalized entries: `data/normalized/<date>/*.yaml` (required).
   - Day summary: `derived/summaries/<date>.yaml` (optional; pass `null` if missing).
   - Microfacts: `derived/microfacts/<date>.yaml` (optional; pass `null` if missing).
   - Recent history: last N summaries/microfacts (configurable, default `N=3`) when available.
   - Current profile and claims.
2. Builds a single prompt payload (see `ProfileUpdatePromptInput` below) and calls `_invoke_structured_llm` with `PromptProfileUpdates` as the response model (re-using retry/timeouts from Stage 4/5).
3. Converts output to `ProfileUpdateProposals` via `convert_prompt_updates_to_proposals`, wraps it inside a `ProfileUpdateBatch`, and writes it to `derived/pending/profile_updates/<date>-<timestamp>.yaml`.

**Helper schema (in-code, not persisted):**

```python
class ProfileUpdatePromptInput(StrictModel):
    date: str
    entries_json: list[dict[str, Any]]
    summary_json: dict[str, Any] | None
    microfacts_json: dict[str, Any] | None
    recent_summaries_json: list[dict[str, Any]]
    recent_microfacts_json: list[dict[str, Any]]
    profile_json: dict[str, Any]
    claims_json: dict[str, Any]
    manifest_json: dict[str, Any] | None
```

We reuse the current `PromptProfileUpdates` DTO for outputs; no schema migration is required.

### Capture Stage & CLI

- Replace Stage 4 runner with `run_profile_update_stage` (new module under `src/aijournal/services/capture/stages/stage4_profile_update.py`). Stage 5 is deleted, and downstream stages shift down one index (index refresh becomes stage 5, persona stage 6, pack stage 7).
- Capture command prints a single OperationResult for the unified stage, listing the pending batch paths and (if `--apply-profile=auto`) how many batches were applied immediately.
- Add `aijournal ops profile update --date YYYY-MM-DD [--timeout --retries --progress]` as the manual entry point; behind the scenes it calls the same pipeline used by capture.
- `aijournal profile apply` remains the review/apply command; it now accepts either explicit `--file` arguments (pointing into `derived/pending/profile_updates/`) or defaults to the latest batch for the supplied date.

### Artifact Layout

- Only one artifact location remains: `derived/pending/profile_updates/<date>-<timestamp>.yaml` containing `ProfileUpdateBatch`.
- Each batch continues to record `inputs` (entry ids, normalized paths, source hashes), `proposals`, `preview`, and metadata (prompt path, model, timestamps). Nothing else writes to a legacy `derived/profile_proposals/` directory once this ships.

## Review & Apply Semantics

1. **Capture default**: generate a batch and leave it for manual review.
2. **Auto-apply mode** (`--apply-profile=auto`): immediately call `apply_profile_update_batch` on the newly created batch(es); record successes/failures in the stage result just as today.
3. **Manual CLI**: `aijournal ops profile update` → `aijournal profile apply --file derived/pending/profile_updates/<batch>.yaml --yes` mirrors the live-mode rehearsal workflow. No second prompt or review command is necessary.
4. **Telemetry**: Stage 4 logs one OperationResult for generation (artifacts = batch paths) and an optional OperationResult for auto-apply. Existing structured events (`prepare_summary`, `pipeline_complete`) remain so tests and run logs stay consistent.

## Documentation & Testing Checklist

- Update `TLDR.md`, `README.md`, `docs/workflow.md`, and `ARCHITECTURE.md` to describe the new stage order and CLI.
- Replace prompt references in `CLAUDE.md` / `AGENTS.md` runbooks.
- Testing (see dedicated plan from agents):
  - Unit tests for the new pipeline (inputs present/missing, fake mode, retry logic).
  - Capture stage tests verifying stage control flags, multi-date runs, and auto-apply behavior.
  - CLI integration tests for `ops profile update` and `profile apply` with the unified format.
  - Regression tests ensuring summaries/microfacts are optional but, when present, flow into the prompt payload.

## Rollout Plan

1. **Phase A (implementation)**: land prompt, pipeline, new stage/CLI; keep old commands as thin wrappers marked deprecated.
2. **Phase B (validation)**: migrate tests/docs, ensure fake-mode fixtures cover the new prompt, remove wrappers.
3. **Phase C (cleanup)**: delete legacy prompt files, stage modules, directories, and CLI commands once the unified stage is green in live-mode rehearsal.

This design satisfies `docs/archive/2025-11-15_prompt3.md` by consolidating the profile update logic into a single, richer stage without destabilizing downstream persistence or review workflows.
