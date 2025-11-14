# Profile Update Legacy Scan

_Updated: 2025-11-14_

This note documents the final `rg 'profile_suggest|characterize'` sweep after the Prompt3
migration. It ensures no active code, tests, or operator-facing docs reference the
removed flows.

## Scan Command

```bash
rg -n "profile_suggest|characterize" --glob '!docs/archive/**' --glob '!.code/**'
```

## Results

- **Active code/tests**: ✅ Clean. The only matches are within historical design docs (`docs/design/profile_update_*.md`) and the Prompt3 spec (`prompt3.md`, `PROMPT3_DESIGN_SUMMARY.md`).
- **Operator docs (README, TLDR, workflow, AGENTS, simulator, risk analysis)**: ✅ Updated to describe the unified `profile_update` stage and `derived/pending/profile_updates/` outputs.
- **Pipelines/CLI**: ✅ `src/aijournal/pipelines/profile_update.py`, the capture stage, simulator validators, CLI commands, and audit tooling now reference only `profile_update`.
- **Legacy mention policy**: Any remaining references appear only inside design documents retained for historical/audit purposes. Each doc includes an explicit status note.

## Next Steps

- Re-run the command above whenever adding new docs or tooling that might mention the legacy stages.
- If new matches appear outside historical docs, treat them as regressions and update this file with the remediation details.
