# Human Simulator & Validation Harness

This guide summarizes the deterministic simulator that stress-tests aijournal's capture pipeline on messy, human-grade inputs.

## Why it exists

- Exercise every capture stage (persist → normalize → summaries → facts → profile_update → index → persona → pack) with realistic Markdown quirks.
- Catch brittle assumptions early via invariant validators (manifest integrity, schema checks, persona/pack freshness, etc.).
- Provide a one-command developer workflow for verifying tolerant ingestion and downstream artifacts without touching production data.

Deeper design logs, fixture matrices, and historical audits now live under `docs/archive/2025-11-simulator/` if you need the long-form rationale.

## Running the simulator

```bash
# full pipeline, temporary workspace cleaned afterward
uv run aijournal ops dev human-sim

# stop after stage 4 and keep the files for inspection
uv run aijournal ops dev human-sim --max-stage 4 --keep-workspace --output /tmp/human-sim-playground
```

Key flags:

- `--max-stage`: limit execution to a specific stage (0–8) for faster debugging.
- `--pack-level`: choose `L1`, `L3`, or `L4` when packs are enabled (defaults to `L1`).
- `--keep-workspace` / `--output`: retain the generated workspace instead of deleting it.

In CI or locally, `uv run pytest tests/simulator/test_human_simulator.py` exercises the same code path with fake LLMs/embeddings for determinism.

## Fixture palette

Each simulator run synthesizes a temp workspace (via `src/aijournal/simulator/fixtures.py`) containing nine representative inputs:

| Label | Highlights |
| --- | --- |
| `good_yaml` | Clean YAML front matter with tags/projects and multiline body |
| `toml_front_matter` | Hugo-style `+++` block with arrays/mixed casing |
| `broken_front_matter` | Missing quotes to trigger tolerant parsing warnings |
| `no_front_matter` | Date only inside the body (`Jan 2, 2025` text) |
| `messy_markdown` | Unclosed bullets, minimal content, odd headers |
| `retro_focus` | Older entry driving history windows and persona context |
| `duplicate_slug` | Reuses an ID to check slug collision handling |
| `weekly-planning` | Longer entry with headings/bullets |
| `history-anchor` | Older-than-a-week anchor to prove index tail rebuilds |

The generator is deterministic, so rerunning the simulator recreates the same files without needing to preserve artifacts.

## Validator coverage

`src/aijournal/simulator/validators.py` enforces invariants per stage:

- **Stages 0–1**: canonical Markdown exists, manifest rows have UTC timestamps, normalized YAML loads as `NormalizedEntry`.
- **Stage 2**: summaries exist for every changed date and contain bullets.
- **Stage 3**: microfacts load, statements stay concise, evidence references valid entries, claim proposals reference real normalized IDs.
- **Stage 4**: profile proposals exist/parse; auto-apply bookkeeping matches artifacts and `profile/claims.yaml` remains valid.
- **Stage 4**: profile update batches load, auto-review tracking avoids duplicate/applied conflicts, pending files actually exist.
- **Stage 6**: index db/annoy/meta files exist, touched dates cover changes, metadata contains `updated_at`.
- **Stage 7**: persona core loads with content and rebuilds when profile changes.
- **Stage 8**: pack artifacts exist, match requested level, and respect token budgets (or explain why they were skipped).

Failures show up as a compact table in the CLI output, pointing to the stage, invariant, date, and file path.

## Related references

- `docs/tolerant-parsing.md`: explains the tolerant front-matter/date parsing utilities used by capture/normalize.
- `docs/migration-tolerant-parsing.md`: checklist for migrating older components to the tolerant helpers.
- Archived research (audits, deep design, historical notes) lives in `docs/archive/2025-11-simulator/` for future archeology.
