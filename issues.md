# Issues observed during 2025-11-13 live run

This report captures the regressions uncovered while running the `aijournal`
CLI against real journal entries (command sequence reproduced via
`aij capture --from ~/example-blog-entries > output.log`). It’s intended to give
future agents full context before attempting fixes or re-runs.

## 1. Characterize stage fails schema validation

- **Command**: `aij capture --from ~/example-blog-entries > output.log`
- **Stage**: Characterize (`capture` stage 5)
- **Error**: `Structured output generation failed for prompts/characterize.md: Model response failed validation after retries … extra_forbidden @ loc ['summary']`
- **Impact**: Capture aborts after stage 4. No characterization batches are
  generated; downstream persona/index refreshes never run. Applying profile
  updates stalls, and `capture` prints `Characterize failed` despite retries.
- **Diagnosis**:
  - `prompts/characterize.md` forbids extra top-level keys, but the live model
    produced `{claims: [...], facets: [...], interview_prompts: [...], summary: ...}`.
  - `_invoke_structured_llm` stops after `DEFAULT_LLM_RETRIES + 1` attempts, so
    this keeps failing in live mode.
- **Open questions**:
  1. Should the prompt remind the model not to emit `summary`, or should the
     schema accept it (e.g., via optional field we strip)?
  2. Should stage 5 expose a `--retries` override (it currently inherits the CLI
     flag) or implement adaptive retry messaging when the same field repeats?
- **Next steps**: update either the prompt template or the response model plus
  normalization logic so extra keys are ignored/stripped, then re-run
  `capture --min-stage 5 --max-stage 5 --date <affected-date>` to regenerate the
  pending batches.

## ✅ Fixed: Journal summaries no longer duplicate full body content

- **Resolution**: Stage 0 now derives a deterministic summary when one is
  missing by taking the first paragraph, collapsing whitespace, trimming to 400
  characters, and appending `...` when truncated (see
  `src/aijournal/services/capture/stages/stage0_persist.py`). Existing summaries
  remain untouched so reruns stay idempotent.
- **Tests**: `tests/services/capture/test_summary_policy.py` covers “missing
  summary”, “existing summary”, and “long body with ellipsis” scenarios.
- **Docs**: README + `docs/workflow.md` describe the policy for future runs.
- **Outcome**: Imported Markdown stays readable and downstream prompts receive a
  concise synopsis without extra LLM calls.

## ✅ Fixed: Claim proposals now get stable unique IDs

- **Resolution**: `_proposal_claim_id` now appends an 8-character SHA-256 hash of
  the normalized statement to the first `normalized_id` (or slug fallback),
  ensuring each statement/entry pair receives a unique, deterministic ID. This
  change lives in `src/aijournal/commands/profile.py`.
- **Tests**: `tests/services/test_claim_id_generation.py` asserts (a) multiple
  proposals from the same normalized entry get unique IDs and (b) `_apply_claim_proposal`
  keeps every statement without overwriting prior claims.
- **Outcome**: `profile/claims.yaml` now contains every accepted statement from a
  date, eliminating the silent overwrite that previously occurred.

## Pending decisions / follow-ups

1. **Characterize schema** – Decide whether to loosen the schema or reinforce
   the prompt, then patch stage 5 to prevent repeated failures.
2. **Summary auto-fill** – ✅ Deterministic first-paragraph summaries now ship in
   stage 0 with tests and docs.
3. **Claim ID validation** – ✅ Stable hashed IDs and regression tests are in
   place; reruns retain all claims.

Please keep this document updated as fixes land, so future agents can see which
issues remain outstanding before starting a live run.
