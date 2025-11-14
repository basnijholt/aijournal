# Outstanding Issues – November 14, 2025

- **Prompt/LLM Contract Hygiene (High)**  
  **Rationale:** During the refactor we hardened profile_update/micro-facts by routing them through prompt-specific DTOs, but new commands can easily regress (e.g., interview/facts previously pointed at runtime models).  
  **Acceptance criteria:** (1) `rg 'response_model=' -n src/aijournal/commands` lists only DTOs (`Prompt*`, `DailySummary`, etc.), never runtime artifacts. (2) A short checklist lives in `CONTRIBUTING.md` reminding contributors to add DTO + converter whenever a new structured prompt is introduced. (3) CI includes a lightweight test (or script) that fails if a runtime model is used directly.  
  **Actions:** add the checklist, add an automated grep-based check, and document the DTO boundary so future work stays clean.

- **Micro-facts quality & metadata leakage (Medium)**  
  **Rationale:** Even with the DTO layer, live runs still produce metadata-only claims/facts (e.g., “entry created on …”, “title is …”) and hallucinations such as “Author born on 2011‑02‑20” despite the body describing metaphorical rebirth. These dilute downstream claims.  
  **Acceptance criteria:** (1) Prompt explicitly forbids metadata-only statements and the converter drops them (unit test asserting unwanted IDs like `*-entry-created`). (2) End-to-end test feeds a sample entry with rich text and asserts at least one fact references paragraph content (via `raw_markdown`). (3) Manual QA log shows micro-facts referencing concrete sentences, not front-matter.  
  **Actions:** tighten `prompts/extract_facts.md`, add filtering in `generate_microfacts`, and cover with regression tests.

- **Profile update resilience (High)**  
  **Rationale:** The unified stage still flakes in two ways: (a) DTO rejects optional fields like scope/provenance, yielding `extra_forbidden` errors; (b) very large entries (e.g., 8 KB blog posts) hit timeouts or Ollama JSON parse errors. Both leave capture half-finished.  
  **Acceptance criteria:** (1) DTO/converter tolerate optional scope/provenance fields and strip unsupported keys before validation. (2) The profile update runner escalates timeout/attempts for large entries (e.g., adaptive retries or chunking). (3) Running `uv run aijournal capture --from ~/example-blog-entries` completes the profile update stage for all dates without manual retries, with the run log showing either success or graceful degradation per-entry instead of overall failure.  
  **Actions:** relax DTO, add tolerant converter, implement adaptive retry/timeout logic (or chunk long bodies), and expand tests to cover both schema and long-entry scenarios.

- **Command/docs drift for `ops pipeline extract-facts` (Low)**  
  **Rationale:** The command is deprecated, yet our troubleshooting instructions still recommend it; running it in a fresh workspace emits “No normalized entries” and confuses operators.  
  **Acceptance criteria:** (1) Documentation points users to `capture --min-stage 3 --max-stage 3` instead. (2) The command either becomes a thin wrapper around capture or prints a clearer guidance message. (3) `issues.md` entry closed once docs/tests updated.  
  **Actions:** update docs/workflow.md + CLI help and ensure acceptance tests cover the recommended workflow.

- **Prompt logging ergonomics (Low)**  
  **Rationale:** We can log prompts via `sitecustomize`, but it’s ad-hoc. Engineers need a first-class flag (e.g., `AIJOURNAL_TRACE_PROMPTS=1`) that writes prompts/replies into `derived/logs/structured_prompts.log`.  
  **Acceptance criteria:** (1) Setting the env var produces per-call entries (command, prompt path, prompt JSON, reply JSON). (2) Running without the flag imposes no overhead. (3) Docs reference the feature for live runs.  
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

## Profile update flakes on large entries (⚠ ongoing)
- Live run 2025‑11‑13: 8KB Dutch entry timed out/hit JSON parse errors. Even after DTO fixes, long entries still require adaptive timeouts or chunking; see “Profile update resilience” above for acceptance criteria.
