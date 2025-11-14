# Outstanding Issues – November 14, 2025

- **Profile update resilience (High)**  \\
  **Rationale:** The unified stage still flakes in two ways: (a) DTO rejects optional fields like scope/provenance, yielding `extra_forbidden` errors; (b) very large entries (e.g., 8 KB blog posts) hit timeouts or Ollama JSON parse errors. Both leave capture half-finished.  \\
  **Acceptance criteria:** (1) DTO/converter tolerate optional scope/provenance fields and strip unsupported keys before validation. (2) The profile update runner escalates timeout/attempts for large entries (e.g., adaptive retries or chunking). (3) Running `uv run aijournal capture --from ~/example-blog-entries` completes the stage for all dates without manual retries, with the run log showing either success or graceful degradation per entry.  \\
  **Actions:** relax the DTO, add tolerant converters, implement adaptive retry/timeout logic (or chunk long bodies), and expand tests to cover both schema and long-entry scenarios.

- **Command/docs drift for `ops pipeline extract-facts` (Low)**  \\
  **Rationale:** The command is deprecated, yet our troubleshooting instructions still recommend it; running it in a fresh workspace emits “No normalized entries” and confuses operators.  \\
  **Acceptance criteria:** (1) Documentation points users to `capture --min-stage 3 --max-stage 3` instead. (2) The command either becomes a thin wrapper around capture or prints clearer guidance. (3) This entry can be closed once docs/tests reflect the recommended workflow.  \\
  **Actions:** update docs/workflow.md + CLI help and ensure acceptance tests cover the recommended workflow.

- **Prompt logging ergonomics (Low)**  \\
  **Rationale:** We can log prompts via `sitecustomize`, but it’s ad hoc. Engineers need a first-class flag (e.g., `AIJOURNAL_TRACE_PROMPTS=1`) that writes prompts/replies into `derived/logs/structured_prompts.log`.  \\
  **Acceptance criteria:** (1) Setting the env var produces per-call entries (command, prompt path, prompt JSON, reply JSON). (2) Running without the flag imposes no overhead. (3) Docs reference the feature for live runs.  \\
  **Actions:** wrap the current hook in a supported feature and document it.

Update this list as fixes land so future agents know which items remain.

---

# Resolved / Historical Context

## Date field not recognized from imports (✅ fixed in 390a352)
- **Command:** `uv run aijournal capture --from ~/example-blog-entries`
- **Stage:** Stage 0 (persist) misread Jekyll/WordPress `date` fields and fell back to filenames, so every imported entry landed under the wrong `YYYY/MM/DD` bucket. We now normalize common aliases (`date`, `published`, etc.) into `created_at` before inference and added tests in `test_stage_persist.py`.

## Opaque LLM errors (✅ fixed in 52591cf)
- Capture previously printed `stage exited with code 1` without the underlying `LLMResponseError`. Commands now chain `typer.Exit` and the graceful wrappers unwrap the cause, so operators see the real timeout/schema message.

## Summaries duplicated full body (✅ fixed)
- Stage 0 now synthesizes a ≤400‑char first paragraph when `summary` is absent, leaving existing summaries untouched. Tests: `tests/services/capture/test_summary_policy.py`.

## Claim proposals reused IDs (✅ fixed)
- `_proposal_claim_id` now appends an 8‑char SHA of the normalized statement, preventing overwrites when multiple proposals share the same `normalized_id`. Regression test: `tests/services/test_claim_id_generation.py`.

## Prompt/LLM contract hygiene (✅ fixed in current branch)
- Added contributor checklist + workflow/TLDR notes covering DTO rules, added pytest guard `tests/ci/test_prompt_contracts.py`, and ensured Typer commands only point `response_model` at `Prompt*`, `DailySummary`, or `AdviceCard`.

## Micro-facts quality & metadata leakage (✅ fixed in current branch)
- Prompt now warns against metadata-only statements, `convert_prompt_microfacts` filters them via `is_metadata_only_fact`, and regression tests cover the heuristics. Facts referencing only front matter are discarded before persistence.

