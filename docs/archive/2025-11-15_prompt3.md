## Prompt 3 – Unify `profile_suggest` + `characterize` into a single richer profile update step

```text
You are working in the `aijournal` repository.

High-level goal
---------------
Streamline and strengthen the profile update logic by unifying the overlapping `profile_suggest` and `characterize` flows into a single, richer “profile update” step.

Zero backwards-compatibility policy
-----------------------------------
This repository has no external users. Backwards compatibility, feature flags, and deprecated entry points only add noise. Delete obsolete flows outright instead of keeping adapters or “legacy mode” switches. Remove the old `profile_suggest` stage, prompt, and CLI surfaces once the unified stage lands; don’t keep them around “just in case.”

Right now:

- `profile_suggest`:
  - Reads normalized entries + existing profile/claims.
  - Proposes claims and facet updates for a given date.
  - Wrote `derived/profile_proposals/YYYY-MM-DD.yaml`, then `ops profile apply` merged them. (Legacy behavior to be removed.)
- `characterize`:
  - Reads normalized entries + existing profile/claims (and manifest).
  - Proposes claims, facets, and interview prompts.
  - Writes `derived/pending/profile_updates/*.yaml`, which are reviewed/applied.

They are conceptually similar and can compete or duplicate work. We want **one authoritative LLM-driven profile update pass per day** that:

- Has richer context: daily summary, today’s microfacts, and a bit of recent history.
- Emits claims, facet updates, and interview prompts in one go.
- Fits cleanly into the capture pipeline as a single stage.

Context and constraints
-----------------------
- The capture pipeline stages 4 & 5 (see TLDR.md) use:
  - `prompts/profile_suggest.md`
  - `prompts/characterize.md`
- These prompts exist only as reference material; the unified implementation should replace them completely and delete any redundant prompt files.
- `ARCHITECTURE.md` describes:
  - Claim atoms in `profile/claims.yaml`
  - Self profile in `profile/self_profile.yaml`
  - Pending updates under `derived/pending/profile_updates/`
- We also plan (in other tasks) to:
  - Use daily summaries as context.
  - Build a RAG + ChromaDB index over microfacts (so you may assume that eventually we can retrieve related microfacts, but this task should not *depend* on that being done right now).

Design intent
-------------
- There should be exactly one “deep” profile update pass for a given day that:
  - Consumes normalized entries for that day.
   - Consumes the day’s summary (and optionally recent summaries).
   - Consumes that day’s microfacts (and, in the future, related microfacts via RAG).
   - Consumes current profile + claims.
- That pass should:
   - Propose `claims` (ClaimAtomInput-like) and `facets` (set/remove).
   - Produce `interview_prompts` to resolve ambiguities.
- The capture pipeline should treat this as a single stage, not two overlapping ones.

What to implement
-----------------
1. Define the intended “single profile update” pipeline
   - Create or refactor into a pipeline (e.g., conceptual name `profile_update`) that:
     - Takes a date.
     - Loads:
       - Normalized entries for that date.
       - Day summary (`derived/summaries/YYYY-MM-DD.yaml`) when available.
       - Day’s microfacts (`derived/microfacts/YYYY-MM-DD.yaml`) when available.
       - Current profile (`profile/self_profile.yaml`) and claims (`profile/claims.yaml`).
       - Optionally, last N days of summaries/microfacts if you can do so within reasonable context limits.
   - Calls one LLM prompt to produce:
     - `claims`: proposed ClaimAtomInput-like objects.
     - `facets`: path + set/remove operations.
     - `interview_prompts`: questions for follow-up.
   - Replace both existing prompt files. There is no need to keep `prompts/profile_suggest.md` or the old `characterize` template for compatibility—delete whichever content no longer serves the unified spec.

2. Adjust the capture pipeline to use this single stage
   - Today, capture uses:
     - Stage 4: `profile_suggest` + apply.
     - Stage 5: `characterize` + optional review/apply.
   - We want:
     - A single “profile update” stage that:
       - Runs the unified pipeline.
       - Writes pending updates in a consistent format (e.g., the current `derived/pending/profile_updates/*.yaml` style).
       - Optionally auto-applies changes when capture is configured with `--apply-profile` flags.
   - Make sure:
       - The old command surfaces (`aijournal ops profile suggest`, `aijournal ops pipeline characterize`, legacy stage helpers, etc.) are removed or renamed to the new canonical pipeline. Do not leave wrappers or deprecated aliases behind.
       - The default capture path only runs the new stage—there should be zero code paths that still execute the removed `profile_suggest` or `characterize` logic.

3. Preserve review and auditability semantics
   - The unified pipeline should still:
     - Produce an artifact suitable for human review (claims, facets, interview prompts plus enough context).
     - Be applied via an explicit “apply” step (`ops profile apply` or similar).
   - Do NOT auto-edit `profile/self_profile.yaml` or `profile/claims.yaml` without:
     - A clear, existing configuration/flag that allows that.
     - A visible record in `derived/pending/profile_updates/` or equivalent.

4. Keep fake mode and existing tests sane
   - The new unified pipeline must:
     - Work with `AIJOURNAL_FAKE_OLLAMA=1` deterministic fixtures.
     - Integrate into the existing testing story as much as possible.
   - Update or add tests to:
     - Verify that for a given date:
       - Only the unified profile update pipeline runs in the standard capture flow.
       - It reads normalized entries + (when present) summary + microfacts.
       - It emits claims/facets/interview_prompts as expected.

Why we’re doing this
--------------------
- Currently, `profile_suggest` and `characterize` are two separate LLM passes performing overlapping work against the same inputs.
  - This duplicates effort.
  - It risks divergent or conflicting profile updates.
- We want a clear single authority for profile changes:
  - One richer, context-aware pass per day.
  - That pass can leverage:
    - Day summaries.
    - Microfacts.
    - Recent history.
    - Existing profile and claims.
- This should:
  - Simplify the mental model of the system.
  - Reduce drift between different profile-updating agents.
  - Make it easier to reason about why the persona looks the way it does.
```
