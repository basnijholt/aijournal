# aijournal Capture Pipeline - Quick Reference

This document explains each stage in the `aijournal capture` pipeline in simple terms.

---

## **Stage 0: Persist**
**Goal**: Save raw journal entries into canonical Markdown format

**What it does**:
- Writes Markdown files to `data/journal/YYYY/MM/DD/<slug>.md`
- Creates raw snapshots in `data/raw/<hash>.md` (for backup/deduplication)
- Updates manifest (`data/manifest/ingested.yaml`) with entry hashes
- Generates a summary from first paragraph if entry lacks one (≤400 chars)

**Inputs**: Raw text (from stdin, editor, file, or directory)
**Outputs**: Canonical Markdown files with frontmatter (id, date, title, tags, mood, etc.)

**Example**:
```markdown
---
id: "2025-11-14-focus-session"
created_at: "2025-11-14T10:30:00Z"
title: "Morning deep work session"
tags: ["focus", "planning"]
mood: "energized"
---

Blocked 8-10am for deep work on the new feature...
```

---

## **Stage 1: Normalize**
**Goal**: Convert Markdown into structured YAML for processing

**Prompt**: None (deterministic parsing)

**What it does**:
- Parses frontmatter and body into sections
- Extracts metadata (tags, projects, mood, timestamps)
- Creates normalized entry in `data/normalized/YYYY-MM-DD/<id>.yaml`

**Inputs**: Canonical Markdown from Stage 0 (**raw**)
**Outputs**: Structured YAML with metadata + sections

**Example Output**:
```yaml
id: "2025-11-14-focus-session"
created_at: "2025-11-14T10:30:00Z"
title: "Morning deep work session"
summary: "Blocked 8-10am for deep work on the new feature..."
tags: ["focus", "planning"]
mood: "energized"
sections:
  - heading: null
    paragraphs:
      - "Blocked 8-10am for deep work on the new feature..."
```

---

## **Stage 2: Summarize**
**Goal**: Create concise daily summaries

**Prompt**: `prompts/summarize_day.md`
**Summary**: "Compress normalized entries into bullets (≤5), highlights (≤3), and TODOs (≤3). Each item ≤18 words."

**LLM Task**:
1. Scan metadata (date, tags, mood, sections, summaries)
2. Extract bullets (key observations/decisions), highlights (standout moments), todo_candidates (actionable follow-ups)
3. Keep items concise (≤18 words), no speculation

**Inputs**: Normalized entries for the day (**derived** from Stage 1)
**Outputs**: `derived/summaries/YYYY-MM-DD.yaml`

**Example Output**:
```json
{
  "day": "2025-11-14",
  "bullets": [
    "Blocked 8-10am for deep work session",
    "Finalized feature design with team"
  ],
  "highlights": [
    "Breakthrough on authentication flow design"
  ],
  "todo_candidates": [
    "Schedule code review for new auth module"
  ]
}
```

---

## **Stage 3: Extract Facts**
**Goal**: Mine atomic facts and claim proposals from journal entries

**Prompt**: `prompts/extract_facts.md`
**Summary**: "Start from the day summary (bullets/highlights/todos) as your map, then extract atomic facts and claim proposals with evidence."

**Prerequisite**: `derived/summaries/<date>.yaml` must exist. If the daily summary is
missing, rerun Stage 2 (`aijournal ops pipeline summarize --date YYYY-MM-DD`).

**LLM Task**:
1. Read the Stage 2 summary to understand what mattered most today.
2. Verify each highlighted item against the normalized entries and create micro-facts (atomic observations) with confidence scores (0.0-1.0).
3. Optionally propose claims (habits, values, preferences, etc.) with evidence references.
4. Reference specific entry IDs and paragraph indices for every fact/claim.

**Inputs**:
- Day summary (`derived/summaries/<date>.yaml`) — **primary map**
- Normalized entries (**derived** from Stage 1) — **verification source**
**Outputs**: `derived/microfacts/YYYY-MM-DD.yaml`

**Vector DB**: Each validated microfact is immediately embedded and stored in the Chroma-backed microfact index (`derived/microfacts/microfacts/index`). This index is used later (Stage 4+) for deduplication and retrieval of recurring insights.

**Example Output**:
```json
{
  "facts": [
    {
      "id": "morning-focus-block",
      "statement": "User blocked 8-10am for deep work",
      "confidence": 0.8,
      "evidence_entry": "2025-11-14-focus-session",
      "evidence_para": 0,
      "first_seen": "2025-11-14",
      "last_seen": "2025-11-14"
    }
  ],
  "claim_proposals": [
    {
      "type": "habit",
      "statement": "Maintains morning focus blocks for deep work",
      "strength": 0.6,
      "reason": "Mentioned 8-10am deep work block",
      "evidence_entry": "2025-11-14-focus-session",
      "evidence_para": 0
    }
  ]
}
```

> **Contributor note:** Stage 3 calls `_invoke_structured_llm` with `response_model=PromptMicroFacts` and converts the DTO via `convert_prompt_microfacts`. Keep prompts JSON-only and reject metadata-only statements (“entry created on…”, “title is…”). Micro-facts must cite paragraph content through `evidence_entry` / `evidence_para` before they become runtime `MicroFactsFile` entries.

---

## **Stage 4: Profile Update**
**Goal**: Suggest and apply profile updates based on evidence

**Prompt**: `prompts/profile_update.md`
**Summary**: "Review the day summary plus consolidated microfacts and existing persona to propose claim/facet updates with evidence and interview prompts."

**Prerequisite**: Requires the same-day summary from Stage 2. The command aborts
with a remediation hint when `derived/summaries/<date>.yaml` is missing.

**LLM Task**:
1. Read the Stage 2 summary to orient on key themes.
2. Scan consolidated microfacts for recurring evidence.
3. Read existing profile and claims to understand baseline.
4. Dive into normalized entries to verify summarized signals or discover missing evidence.

**Vector DB usage**: Stage 4 consumes the consolidated microfacts produced by the Chroma index to ensure claims reference deduped, canonical statements; the same index also powers the retrieval that feeds interview, persona, and advice prompts.
5. Propose new claims/facets only when entries provide durable support; strengthen or adjust existing claims when confirmed.
6. Provide ≤25 word justifications with evidence references.

**Inputs**:
- Day summary (`derived/summaries/<date>.yaml`) — **primary map**
- Normalized entries (**derived** from Stage 1) — **verification source**
- Current profile (`profile/self_profile.yaml`) (**raw**)
- Current claims (`profile/claims.yaml`) (**raw**)
- Consolidated microfacts (`derived/microfacts/consolidated.yaml` when present)

**Outputs**: `derived/pending/profile_updates/<date>-<timestamp>.yaml`

**Example Output**:
```json
{
  "claims": [
    {
      "type": "habit",
      "statement": "Blocks morning hours for uninterrupted deep work",
      "strength": 0.65,
      "reason": "Three weekly entries show recurring morning focus pattern",
      "evidence_entry": "2025-11-14-focus-session",
      "evidence_para": 0
    }
  ],
  "facets": [
    {
      "path": "planning.focus_blocks.morning",
      "operation": "set",
      "value": "Protects 8:00-10:00 for deep work on weekdays",
      "reason": "Latest entry confirms recurring focus block",
      "evidence_entry": "2025-11-14-focus-session"
    }
  ]
}
```

Capture can **apply** these batches immediately (`--apply-profile=auto`) or queue them for `aijournal ops profile apply`/`ops pipeline review` later.

---

## **Stage 5: Index Refresh**
**Goal**: Keep retrieval/search artifacts aligned with the latest entries and applied profile updates

**Commands**: `aijournal ops index rebuild`, `aijournal ops index tail`

**What happens**:
1. Rebuilds ANN + SQLite indexes for normalized entries referenced in Stage 4 outputs
2. Captures manifest hashes + metadata for reproducibility
3. Surfaces warnings when embeddings are stale or missing

---

## **Stage 6: Index Refresh**
**Goal**: Update retrieval index for search/chat

**Prompt**: None (deterministic chunking + embeddings)

**What it does**:
- Chunks normalized entries (700-1200 chars, sentence-aware)
- Generates embeddings via Ollama (`embeddinggemma:300m`)
- Upserts vectors + metadata into the Chroma collection under `derived/index/chroma`
- Writes `derived/index/meta.json`

**Inputs**: Normalized entries (**derived** from Stage 1)
**Outputs**:
- `derived/index/chroma/` (Chroma persistent store)
- `derived/index/meta.json` (index metadata)
- `derived/index/chunks/YYYY-MM-DD.yaml` (chunk manifests + vector shards)

---

## **Stage 7: Persona Refresh**
**Goal**: Rebuild persona core snapshot

**Prompt**: None (deterministic ranking + selection)

**What it does**:
- Ranks claims by `strength × impact × decay`
- Selects top claims + key facets to fit token budget (~1200 tokens)
- Writes `derived/persona/persona_core.yaml`

**Inputs**:
- Profile (`profile/self_profile.yaml`) (**raw**)
- Claims (`profile/claims.yaml`) (**raw**)

**Outputs**: Compact persona snapshot for chat/packs

**Example Output**:
```yaml
persona:
  values: [...]
  goals: [...]
  boundaries: [...]
claims:
  - type: "habit"
    statement: "Maintains morning focus blocks..."
    strength: 0.75
```

---

## **Stage 8: Pack**
**Goal**: Export context bundles (optional, if `--pack` specified)

**Prompt**: None (deterministic assembly)

**What it does**:
- Assembles L1 (persona), L3 (extended profile), or L4 (full context) packs
- Respects token budgets and trimming rules
- Writes `derived/packs/<level>-<date>.yaml`

**Inputs**:
- Persona core (**derived** from Stage 7)
- Normalized entries (**derived** from Stage 1)
- Summaries (**derived** from Stage 2)
- Profile/claims (**raw**)

**Outputs**: Context bundles for external LLMs

---

## **Quick Reference**

### Stage Categories

**Raw Input Processing (Stages 0-1)**:
- Deal with **raw inputs** (Markdown → YAML)
- No LLM calls, deterministic parsing

**LLM-Driven Derivation (Stages 2-5)**:
- Use **LLM prompts** to derive insights
- Generate summaries, facts, profile updates

**Deterministic Operations (Stages 6-8)**:
- Indexing, ranking, assembly
- No LLM calls, reproducible outputs

### Data Types

**Raw** (human-edited, authoritative):
- `data/journal/**/*.md` - Markdown entries
- `profile/self_profile.yaml` - Persona profile
- `profile/claims.yaml` - Claim atoms

**Derived** (safe to delete/regenerate):
- Everything under `derived/` directory
- Generated from raw inputs via pipelines

### Manual Override

Each stage can be run manually:
```bash
# Stage 1
uv run aijournal ops pipeline normalize data/journal/YYYY/MM/DD/<entry>.md

# Stage 2
uv run aijournal ops pipeline summarize --date YYYY-MM-DD

# Stage 3
uv run aijournal ops pipeline extract-facts --date YYYY-MM-DD

# Stage 4
uv run aijournal ops profile update --date YYYY-MM-DD
uv run aijournal ops profile apply --date YYYY-MM-DD --yes
uv run aijournal ops pipeline review --file <batch>.yaml --apply

# Stage 5
uv run aijournal ops index update --since 7d

# Stage 6
uv run aijournal ops persona build

# Stage 7
uv run aijournal export pack --level Lx [--date YYYY-MM-DD]
```

### Stage Control

Control which stages run:
```bash
# Run only stages 0-1 (persist + normalize)
aijournal capture --text "..." --max-stage 1

# Resume from stage 2 onwards
aijournal capture --from notes/ --min-stage 2

# Skip specific stages
aijournal capture --text "..." --max-stage 5  # Skip index/persona/pack
```
