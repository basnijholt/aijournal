## Prompt 1 – Reuse daily summaries throughout the capture pipeline

```text
You are working in the `aijournal` repository.

High-level goal
---------------
We want downstream steps to *start from* the day-level summary, then verify/refine against the full normalized entries. This should improve stability, reduce duplication, and better exploit my large context window and willingness to spend compute. Backward compatibility is explicitly **not** a goal—simplify aggressively and delete legacy paths.

Context (what exists now)
-------------------------
- The capture pipeline (see TLDR.md) runs:
  - Stage 0: persist → Markdown in `data/journal/...`
  - Stage 1: normalize → YAML in `data/normalized/YYYY-MM-DD/*.yaml`
  - Stage 2: summarize → `derived/summaries/YYYY-MM-DD.yaml` via `prompts/summarize_day.md`
  - Stage 3: extract-facts → `derived/microfacts/YYYY-MM-DD.yaml` via `prompts/extract_facts.md`
  - Stage 4: profile_suggest → `derived/profile_proposals/YYYY-MM-DD.yaml`
  - Stage 5: characterize → `derived/pending/profile_updates/*.yaml`
- `ARCHITECTURE.md`, `docs/workflow.md`, and `TLDR.md` describe these stages and where artifacts live.
- Summaries *are* used in packs (L2/L3/L4), but not really as input to the LLMs that derive facts/claims.

Key design constraints
----------------------
- I have a ~100k token context window and I’m fine with heavy per-day LLM calls.
- Remove legacy/deprecated code instead of retaining dual paths; clarity beats backwards compatibility.
- Everything must remain reproducible and idempotent.
- If summaries are missing, fail fast with a clear error so the operator generates them—no silent fallbacks.

What to implement
-----------------
1. Wire daily summaries into extract-facts
   - For `aijournal ops pipeline extract-facts --date YYYY-MM-DD`:
     - Before calling the LLM, load `derived/summaries/YYYY-MM-DD.yaml` and fail immediately if it is missing.
     - Pass this as an additional JSON input to the LLLM (e.g. `SUMMARY_JSON` or similar), alongside `ENTRIES_JSON`.
   - Prompt-level intent (do NOT hard-code wording; adjust where appropriate):
     - The model should treat the summary as a “map of what mattered today,” then go to the full entries to:
       - Confirm those points
       - Extract atomic microfacts
       - Avoid missing big items that are clearly in the summary
     - The full entries remain the ground truth.

2. Wire daily summaries into profile_suggest
   - For `aijournal ops profile suggest --date YYYY-MM-DD`:
     - Load the same `derived/summaries/YYYY-MM-DD.yaml`, enforcing its presence just like Stage 3.
     - Provide it to the LLM along with:
       - Normalized entries for the day
       - Current profile (`profile/self_profile.yaml`)
       - Current claims (`profile/claims.yaml`)
   - Intention:
     - Use bullets/highlights as high-level hypotheses about important patterns/goals/values.
     - Only turn them into claims/facets when they are supported by the actual entries.
   - Summaries are mandatory inputs; missing data should halt the command with a clear remediation message.

3. Wire daily summaries into characterize (and optionally interview)
   - For `aijournal ops pipeline characterize --date YYYY-MM-DD`:
     - Feed in the same day summary as an extra JSON block and abort if it cannot be loaded.
     - Optionally also load the previous N days’ summaries (e.g. last 7 days) when within budget, so characterize can see trends over multiple days.
       - This is allowed because we have a large context window and low sensitivity to compute cost.
   - For `aijournal ops profile interview --date YYYY-MM-DD`:
     - It’s acceptable (and desirable) to reuse the last N days’ summaries to drive better questions about recent themes.

4. Remove backward-compatibility fallbacks
   - Pipelines should:
     - Assume summaries exist; if not, raise a helpful error directing the operator to rerun Stage 2.
     - Delete legacy code paths that attempted to operate without summaries.
   - Add tests/fixtures that prove:
     - Summaries are being plumbed into the pipelines.
     - Missing summaries trigger a clear failure (e.g., custom exception) instead of silently degrading behaviour.

Why we’re doing this
--------------------
- Right now, Stage 2 produces a good day-level distillation that is barely used by later LLM passes.
- Later stages (facts, profile_suggest, characterize) independently rediscover the same patterns from raw normalized entries.
- With a large context window and no strong compute constraint, it’s strictly better to:
  - Give the LLM the day’s structured summary as a “cheat sheet”.
  - Let it verify/refine against full text, instead of rediscovering everything from scratch.
- This should:
  - Make microfacts more complete and focused.
  - Improve consistency across profile updates and characterization.
  - Reduce the risk that important events are missed by downstream agents.
```
