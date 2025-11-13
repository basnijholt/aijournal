# Human Simulator Fixture & Invariant Plan

**Last updated:** 2025-11-12

The human simulator now seeds a deterministic workspace that exercises the
entire capture pipeline (stages 0–8). This note documents the expanded fixture
set and the invariants each stage validator enforces so future changes stay
grounded in real-world workflows.

## Fixture Matrix

| Label | Date | Purpose |
|-------|------|---------|
| `good_yaml` | 2025-01-05 | Canonical YAML front matter with tags/projects/mood |
| `toml_front_matter` | 2025-01-04 | TOML/Hugo syntax coverage |
| `broken_front_matter` | 2025-01-03 | Malformed YAML to trigger tolerant parser |
| `no_front_matter` | 2025-01-02 | Body-only entry with inline date |
| `messy_markdown` | 2025-01-01 | Poorly formatted Markdown (lists/quotes) |
| `retro_focus` | 2024-12-28 | Entry older than 7 days to hit index tail logic |
| `duplicate_slug` | 2024-12-30 | Reuses `id` to test slug collision warnings |
| `weekly-planning` | 2024-12-31 | Long-form headings/bullets for rich summaries |
| `history-anchor` | 2024-12-27 | Very old entry to keep history packs honest |

These nine files cover:

1. Every front-matter style (YAML, TOML, JSON-like, none).
2. Multi-day spans (>7 days) to ensure `stage6_index` exercises the `since`
   window and L4 pack history windows.
3. Conflicting IDs (`duplicate_slug`) to surface alias warnings in stage 0 and
   to feed characterize/profile stages with overlapping claims.
4. Persona/pack gating by combining `apply_profile="auto"`, persona rebuild,
   and an explicit `pack_level` request.

## Validator Invariant Highlights

| Stage | Invariant(s) |
|-------|--------------|
| 3 – Facts | Micro-facts must reference existing normalized IDs; statements stay ≤500 chars; claim proposals point to valid normalized IDs |
| 4 – Profile | `derived/profile_proposals/<date>.yaml` present & valid; self profile and claims load; auto-apply recorded whenever profile artifacts change |
| 5 – Characterize | Batch bundles parse; no duplicate batches per run; `derive.review` must track applied vs pending sets without overlap |
| 6 – Index | `index.db`, `annoy.index`, `meta.json` exist; meta JSON touches every changed date and records `updated_at` |
| 7 – Persona | Persona core artifact loads and contains content; profile changes must result in a persona rebuild |
| 8 – Pack | Pack artifacts recorded when persona changes; bundle level matches stage details and respects token budgets |

Each validator fails fast (severity `error`) for missing artifacts and emits
`warning` severities for degradations that do not block captures (e.g., a pack
exceeding its token allotment).

## Determinism

- Simulator always forces fake Ollama (`AIJOURNAL_FAKE_OLLAMA=1`).
- `HumanSimulator` freezes time at `2025-01-05T09:00Z` to keep manifests,
  slugs, and derived timestamps stable.
- Pack level defaults to `L1`, but the CLI flag can request `L3/L4` when manual
  testing requires larger windows.

## Future Fixtures

- **Conflicting profile suggestions**: Add a second `weekly-planning` variant
  to deliberately propose overlapping claim scopes and ensure the validators
  catch duplicates when we tighten the characterize review loop.
- **Persona unchanged with explicit pack**: Introduce a CLI smoke test that
  requests a pack while forcing `max_stage=6`. The validator already treats the
  "persona unchanged" message as informational, but having a fixture-driven
  test will prevent regressions.

This plan should keep the simulator anchored to realistic, multi-day workflows
while giving future contributors a checklist for extending invariants.
