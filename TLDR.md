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
**Summary**: "Extract atomic facts with confidence scores, plus optional claim proposals (habits/values/goals) with evidence."

**LLM Task**:
1. Create micro-facts (atomic observations) with confidence scores (0.0-1.0)
2. Optionally propose claims (habits, values, preferences, etc.) with evidence references
3. Reference specific entry IDs and paragraph indices

**Inputs**: Normalized entries (**derived** from Stage 1)
**Outputs**: `derived/microfacts/YYYY-MM-DD.yaml`

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

**Prompt**: `prompts/profile_suggest.md`
**Summary**: "Review entries and existing persona, propose claim/facet updates with evidence. Keep suggestions grounded, no speculation."

**LLM Task**:
1. Read existing profile and claims to understand baseline
2. Propose new claims or facet updates based on journal evidence
3. Strengthen/adjust existing claims when new evidence confirms them
4. Provide ≤25 word justifications with evidence references

**Inputs**:
- Normalized entries (**derived** from Stage 1)
- Current profile (`profile/self_profile.yaml`) (**raw**)
- Current claims (`profile/claims.yaml`) (**raw**)

**Outputs**: `derived/profile_proposals/YYYY-MM-DD.yaml`

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

Then **applies** these suggestions to `profile/self_profile.yaml` and `profile/claims.yaml` (when `--apply-profile=auto`).

---

## **Stage 5: Characterize & Review**
**Goal**: Generate comprehensive profile updates and optionally auto-apply them

**Prompt**: `prompts/characterize.md`
**Summary**: "Read entries, compare with existing persona, propose claim/facet updates + interview questions. More comprehensive than profile_suggest."

**LLM Task**:
1. Generate claims, facets, and interview_prompts
2. Create batches for human review
3. System auto-applies batches generated during current capture run (when `--apply-profile=auto`)

**Inputs**: Same as Stage 4 (normalized entries + existing profile/claims)
**Outputs**: `derived/pending/profile_updates/<date>-<timestamp>.yaml`

**Example Output**:
```json
{
  "claims": [...],
  "facets": [...],
  "interview_prompts": [
    "What triggers changes to the 8-10am deep work block?"
  ]
}
```

---

## **Stage 6: Index Refresh**
**Goal**: Update retrieval index for search/chat

**Prompt**: None (deterministic chunking + embeddings)

**What it does**:
- Chunks normalized entries (700-1200 chars, sentence-aware)
- Generates embeddings via Ollama (`embeddinggemma:300m`)
- Updates SQLite FTS5 database + Annoy index
- Writes `derived/index/meta.json`

**Inputs**: Normalized entries (**derived** from Stage 1)
**Outputs**:
- `derived/index/index.db` (SQLite with FTS5)
- `derived/index/annoy.index` (vector embeddings)
- `derived/index/chunks/YYYY-MM-DD.yaml` (chunk manifests)

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
uv run aijournal ops profile suggest --date YYYY-MM-DD
uv run aijournal ops profile apply --date YYYY-MM-DD --yes

# Stage 5
uv run aijournal ops pipeline characterize --date YYYY-MM-DD
uv run aijournal ops pipeline review --file <batch>.yaml --apply

# Stage 6
uv run aijournal ops index update --since 7d

# Stage 7
uv run aijournal ops persona build

# Stage 8
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
