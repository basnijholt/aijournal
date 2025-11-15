# Prompt Evaluation Report

Capture run workspace: `/tmp/aijournal_capture_eval_20251114`

## 1. Overview
- `/tmp/aijournal_capture_eval_20251114` was initialized with `aijournal init` and `config.yaml` updated to `model: "gpt-oss-20b-128k:latest"`. Summaries confirm the override (e.g., `derived/summaries/2006-12-01.yaml`, `derived/summaries/2011-02-13.yaml`).
- `uv run aijournal capture --from ~/example-blog-entries` ingested five Markdown entries (2006-12-01, 2011-02-13/17/19/20), refreshed the Chroma index, and produced summaries, microfacts, profile updates, persona, and index metadata.
- Stage 0–3 artifacts (Markdown, normalized YAML, summaries) were mostly accurate. Stages 3+ (microfacts → profile updates → persona) surfaced schema issues, duplicate statements, and missing evidence.

## 2. High-Level Successes
- **Summaries mirrored source tone** on richer entries—`derived/summaries/2011-02-19.yaml` captures the partybus chaos while remaining concise.
- **Index metadata** confirms a successful rebuild with expected models and touched dates (`derived/index/meta.json`).
- **Persona builder** generated `derived/persona/persona_core.yaml`, demonstrating the full pipeline is wired end-to-end.

## 3. High-Level Failure Modes
- **Microfacts** contained metadata and empty evidence spans (e.g., `derived/microfacts/2006-12-01.yaml`, `derived/microfacts/2011-02-17.yaml`), making downstream claims unverifiable.
- **Profile updates** frequently violated schemas (`derived/logs/structured_failures/aijournal-profile-update/2025-11-15T01-42-35Z.json`) and invented facet paths (`derived/pending/profile_updates/2011-02-19-2025-11-15T01-43-02Z.yaml`).
- **Persona core** ingested tentative, unreviewed claims, resulting in noisy statements such as “experiences jealousy toward ex” (`derived/persona/persona_core.yaml`).
- **Interview/advise prompts** were not exercised in this run, so their quality depends entirely on the problematic upstream artifacts.

## 4. Prompt-by-Prompt Findings

### 4.1 `prompts/summarize_day.md`
**What worked**
- Entries with substantial content yield well-structured summaries (e.g., `derived/summaries/2011-02-19.yaml` vs. `data/journal/2011/02/19/2011-02-19-partybus.md`).

**Failure modes**
- Sparse entries receive overly minimal output; `derived/summaries/2011-02-17.yaml` and `derived/summaries/2011-02-20.yaml` barely expand on their sources (`data/journal/2011/02/17/...`, `data/journal/2011/02/20/...`).
- TODO candidates often become boilerplate (“Review follow-ups from Partybus”), offering no actionable direction.

**Recommendations**
- Enforce minimum detail (e.g., ≥4 bullets unless the entry is shorter than a threshold).
- Require TODOs to be verb + object and disallow placeholders like “review follow-ups.”

### 4.2 `prompts/extract_facts.md`
**What worked**
- Major events (partybus, breakup, physics analogy) were recognized and logged (`derived/microfacts/2011-02-13.yaml:374-400`).

**Failure modes**
- Evidence spans are empty in nearly every record (see `derived/microfacts/2006-12-01.yaml:3-30`).
- Metadata becomes “preferences” (e.g., `blog-created-date` in `derived/microfacts/2011-02-17.yaml:266-297`).
- Duplicate paraphrasing bloats the files (multiple honesty claims in `derived/microfacts/2006-12-01.yaml:115-183`).

**Recommendations**
- Update the prompt to forbid metadata statements, require at least one span per fact, and collapse duplicates based on IDs.

### 4.3 `prompts/profile_update.md`
**What worked**
- When schemas matched, the prompt produced reasonable claims (e.g., `derived/pending/profile_updates/2011-02-17-2025-11-15T01-42-35Z.yaml:18-45`).

**Failure modes**
- Schema violations: unsupported `type` values like `belief` and arbitrary facets such as `preferences.techno_beats` triggered retry loops (`derived/logs/structured_failures/...`).
- Low-value, redundant claims (“Spent ~250 euros on ex,” “Talked with ex's sister”) saturate the proposals (`derived/pending/profile_updates/2006-12-01-2025-11-15T01-41-34Z.yaml:65-137`).

**Recommendations**
- Embed schema definitions in the prompt, explicitly listing allowed `type` enums and facet paths.
- Penalize or filter proposals without new evidence (e.g., direct metadata).

### 4.4 `prompts/interview.md`
**Run observations**
- Not exercised during this capture run, but interview quality depends on reliable microfacts and profile updates. Existing noise would propagate to interview questions.

**Recommendations**
- After fixing extract_facts/profile_update, re-run interviews to ensure prompts generate targeted, evidence-backed questions.

### 4.5 `prompts/advise.md`
**Run observations**
- Not triggered in this run. Advisor responses would have consumed the noisy persona core, so issues upstream need resolution first.

**Recommendations**
- Once persona generation uses reviewed claims only, evaluate `advise.md` with live runs, ensuring references map back to confirmed claims.

### 4.6 `prompts/extract_facts.md` (reiterated for emphasis)
- Critical to fix evidence spans and metadata leakage before other prompts can succeed.

## 5. Positive Observations Worth Preserving
- Summaries adhere to concision rules; maintain this while adding nuance.
- Microfacts at least capture the existence of major events; with stricter instructions, they can become actionable.
- Profile updates already interpret themes (love, honesty); guardrails should focus on schema alignment, not overturning the core logic.
- Persona builder logs token budgets and trim info, aiding debugging once inputs stabilize.

## 6. Recommendations for Further Tuning
1. **Summaries**: Provide explicit “bad/good” examples (use `derived/summaries/2011-02-17.yaml` vs `derived/summaries/2011-02-19.yaml`) to show desired richness.
2. **Extract Facts**: Require spans, ban metadata, cap duplicates by linking to unique IDs (reference `derived/microfacts/2006-12-01.yaml`).
3. **Profile Updates**: Embed schema enumerations and sanctioned facet paths; reject any outputs with extra keys before writing.
4. **Persona Builder**: Consume only reviewed/accepted claims to avoid injecting tentative, same-day statements.
5. **Interview/Advise**: After upstream cleanup, add evaluation runs that log question/answer alignment to evidence.

