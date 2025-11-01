# Risk Analysis: Capture Pipeline Impact (GPT-5 Summary)

## Objective

Document whether a single `aijournal capture` invocation can unintentionally
overhaul downstream artifacts—specifically `profile/claims.yaml` and
`derived/persona/persona_core.yaml`—so that future reviewers can audit the
pipeline quickly.

## Conclusions

1. **No-op captures are inert.** When capture content dedupes against existing
   hashes, stages ≥2 never execute, leaving summaries, microfacts, profile, and
   persona untouched. (`src/aijournal/services/capture/__init__.py:465-566`)
2. **Profile changes remain scoped.** Stage 4 only applies the claim/facet
   proposals produced for the affected dates; consolidation merges evidence or
   toggles the targeted facet instead of rewriting the full profile.
   (`src/aijournal/services/capture/stages/stage4_profile.py:15-139`,
   `src/aijournal/commands/profile.py:704-746`,
   `src/aijournal/services/consolidator.py:110-239`)
3. **Persona rebuilds are gated.** Stage 7 runs only when persona was already
   stale or capture wrote profile updates. The builder simply re-ranks existing
   claims under deterministic scoring, so downstream shifts mirror the latest
   accepted evidence rather than arbitrary overwrite.
   (`src/aijournal/services/capture/__init__.py:737-803`,
   `src/aijournal/services/capture/stages/stage7_persona.py:17-112`,
   `src/aijournal/pipelines/persona.py:60-204`)
4. **Residual risk = LLM proposal quality.** If the model proposes an incorrect
   facet update (e.g., removal via `_remove_profile_path`), that decision is
   applied verbatim once auto-approval is enabled. Impact remains limited to the
   specific path/claim referenced in the proposal but still warrants human
   review for high-stakes work. (`src/aijournal/commands/profile.py:548-568`)

## Evidence Trace

- Capture stage gating & change detection:
  `src/aijournal/services/capture/__init__.py:335-868`
- Profile apply flow & consolidation:
  - `src/aijournal/services/capture/stages/stage4_profile.py:15-139`
  - `src/aijournal/services/capture/utils.py:184-241`
  - `src/aijournal/commands/profile.py:704-746`
  - `src/aijournal/services/consolidator.py:110-360`
- Persona freshness + rebuild logic:
  - `src/aijournal/services/capture/__init__.py:677-803`
  - `src/aijournal/services/capture/stages/stage7_persona.py:17-112`
  - `src/aijournal/commands/persona.py:64-204`
  - `src/aijournal/pipelines/persona.py:60-204`

## Suggested Follow-ups

1. Add lightweight regression tests around `apply_profile_update_batch` to
   confirm multi-claim proposals can’t silently drop unrelated claims.
2. Consider a dry-run flag for `run_capture` to emit the planned stage impacts
   before applying proposals when operating in live environments.
3. Evaluate adding guardrails in `profile.apply` to require explicit operator
   confirmation for destructive facet removals (high-impact paths like values
   or goals).

