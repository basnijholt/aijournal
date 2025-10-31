# PLAN.md — aijournal (Local‑First, YAML‑Centric) v0.3

This document is the single source of truth for how the agent (and humans) build the
system. When in doubt, follow the plan—no additional approvals are needed to run
commands, execute Ollama calls, or inspect artifacts while implementing it.

A complete, self‑contained blueprint to implement a private, offline, reproducible personal self‑modeling journal using local Ollama. Primary data stays in human‑readable files (YAML/Markdown). All indexes/summaries are reproducible artifacts. Includes an Advisor Mode to give personalized, constraint‑aware advice using your stored profile and claims.

---

# How I explain it to others

I've been working on a project, `aijournal`, inspired by a simple idea: What if a diary could completely know you?

Imagine a journal that learns from your entries to continuously maintain a detailed description of your character—your motivations, goals, ambitions, and values—and links every insight to the real artifacts you've provided. In `aijournal`, everything is stored in plain text, so it’s completely transparent and under your control.

As it grows with you, it asks probing questions tailored specifically to you, helping you reflect and achieve your goals. Over time, it condenses the totality of who you are into a rich, structured self-model.

And here’s why that’s so powerful: We all know that getting useful advice from an LLM requires providing the right context. **Context is everything.** `aijournal` builds that context for you, so you can easily share your self-model with any AI to brainstorm decisions without starting from scratch every single time.

Because all descriptions are just plain text files managed by Git, you can even go back to any point in time and ask questions of your younger self. This is my take on what an AI journal is meant to be.

---

## 1. Vision and Principles

- Private and offline: runs entirely on localhost with Ollama.
- Authoritative data in YAML/Markdown; derived artifacts are reproducible.
- KISS: type-hinted Python with Pydantic models; small, composable commands.
- Primary outcome: a living personality sketch that captures motivations, values, goals,
  likes/dislikes, character traits, and behavioral anti-goals so downstream copilots
  can reason about “you” with minimal prompting.
- Evidence-linked profile with confidence, provenance, and freshness—**automatically maintained by AI** based on ingested sources.
- Hierarchical memory that fits in context (L1→L4).
- Interviewer asks targeted, low-friction follow-ups to close gaps.
- Advisor Mode produces actionable recommendations aligned with your values, goals, boundaries, and coaching preferences.
- Frequent commits; tests first where sensible; fake LLM mode for CI.
- NEVER amend commits or change history! Just add new commits to fix mistakes.

### 1.1 Implementation guardrails

- Keep the stack brutally simple: one ingestion path, one characterization pipeline, one
  review/apply flow. Prefer pure functions + YAML I/O over deep abstraction layers.
- Every new feature must ship with tests (Pytest + fake LLM fixtures) and model validation;
  no hidden magic.
- Derived data must be inspectable and reproducible—if we cannot diff it, we don’t ship it.
- Optimize for a clean end-to-end path to “LLM understands my personality” before adding
  optional niceties.
- **No legacy compatibility:** there are no production users; assume fresh data. Do not carry
  migration code paths or toggles—regenerate artifacts instead of supporting older schemas, and
  fail fast instead of adding defensive fallbacks for outdated formats.

### 1.2 Operating norms

- The automation agent (or human maintainer) may freely run Ollama commands, invoke any CLI
  surface, and ingest `/home/basnijholt/Work/nijho.lt/content/post` without asking for
  permission each time. Running commands, inspecting outputs, and validating data is expected.
- Always propagate learnings back into the YAML profile/claims so future chats can load the
  context bundle (PLAN target: pack L3/L4) and immediately “know” you.

### 1.3 Updated objectives (v0.3)

- **Automated ingestion pipeline**: `aijournal ingest` accepts directories or explicit file lists (blogs, journals, chats), calculates SHA-256 hashes, stores raw snapshots, and records provenance in `data/manifest/ingested.yaml` so the same artifact is never processed twice.
- **Normalization + ETL**: the ingestion step produces normalized YAML entries with rich sections, tags, and summaries that are ready for characterization.
- **AI-maintained personality sketch**: `aijournal characterize` (to be implemented) replaces the `_fake_*` helpers with real LLM integrations that continuously update `profile/self_profile.yaml` and `profile/claims.yaml` with evidence citations, confidence, and freshness timers. The output should be detailed enough to drop into any LLM chat as context and immediately convey motivations, goals, habits, boundaries, and coaching preferences.
- **Review/approval loop**: `aijournal review-updates` lets you inspect proposed facet/claim changes, showing which hashes/evidence generated them before acceptance.
- **Data interaction model**: multiple files reinforce or challenge traits via weighted aggregation; conflicting evidence is captured as ambiguity metadata so Advisor/Interview modes can highlight it.
- **Reproducible safety**: every derived artifact references the manifest hash(es) it used, enabling pack context to prove exactly which sources shaped each trait.

### 1.4 Persona-first addendum (v0.3+)

Feedback from the principal engineer doubles down on "make it really know me" requirements:

- Claims become **typed atoms with scope** so downstream agents can reason about `type/subject/predicate/value` pairs instead of prose.
- Retrieval is **first-class**: normalized entries are chunked, embedded, and indexed (SQLite FTS + Annoy) and all higher-level features consume retrieval APIs.
- A compact, deterministic **persona core** file (L1) is regenerated whenever profile data changes.
- Chat + advisor surfaces must cite claims or journal evidence and write learnings back through consolidation utilities.
- L1–L4 terminology is standardized everywhere (README, CLI help, pack outputs, persona builder).

Non‑goals v0.3:
- No cloud dependencies, no multi‑user tenancy, no real‑time UI (CLI + local HTTP later if needed).

---

## 2. Repository Layout

Authoritative vs derived are physically separated. Authoritative files are the source of truth; derived files can be deleted and fully regenerated.

```
aijournal/
  README.md
  PLAN.md
  pyproject.toml
  justfile                      # optional helper; all tasks call `uv`
  .gitignore
  src/aijournal/                # Python package
    __init__.py
    cli.py
    models/                     # Pydantic schemas + serialization helpers
    services/                   # ollama client, derivation, ranking, advisor
    io/                         # YAML/MD I/O, path mappers
    prompts/                    # prompt loaders, hashing
  tests/                        # unit + functional + fixtures
    fixtures/
  config/
    config.yaml                 # model, paths, temps
    interviews.json
    advice.json
    models.lock.yaml            # optional model digests for reproducibility
  data/                         # authoritative human-authored
    journal/YYYY/MM/DD/*.md     # MD with YAML frontmatter
    normalized/YYYY-MM-DD/*.yaml
  profile/                      # authoritative self-model
    claims.yaml                 # evidence-linked claims
    self_profile.yaml           # traits/values/goals/etc with provenance
  prompts/                      # text/markdown prompt templates (authoritative)
    summarize_day.md
    extract_facts.md
    profile_suggest.md
    profile_probe.md
    advise.md                   # Advisor Mode prompt
  derived/                      # reproducible artifacts (regenerate any time)
    summaries/YYYY-MM-DD.yaml
    microfacts/YYYY-MM-DD.yaml
    profile_suggestions/YYYY-MM-DD.yaml
    interviews/YYYY-MM-DD.yaml
    advice/YYYY-MM-DD/<id>.yaml
    pending/profile_updates/<timestamp>.yaml
    persona/persona_core.yaml   # deterministic ≤1200 token persona core (L1)
    index/
      index.db                  # SQLite manifest + FTS5
      annoy.index               # Annoy ANN vectors
      meta.json                 # embedding metadata
```

### 2.2 Memory layers (canonical definitions)

- **L1 – Persona Core:** `derived/persona/persona_core.yaml` + top accepted claims; always present.
- **L2 – Recent Activity:** last 7 days of summaries, micro-facts, and today's normalized entries.
- **L3 – Extended Profile:** full accepted claims plus extended `self_profile` facets (trimmed deterministically).
- **L4 – Background:** prompts, config, and raw journals for the base day ± optional history.

`pack` composes these layers under a hard token budget with deterministic trimming metadata.

### 2.1 Ingestion + Characterization Pipeline (new for v0.3)

```
sources/ (user files) ──▶ aijournal ingest ──┬── manifest entries (data/manifest/ingested.yaml)
                                             ├── optional raw snapshot (data/raw/<hash>.md)
                                             └── normalized entry (data/normalized/YYYY-MM-DD/<id>.yaml)

normalized entries + manifest + profile ──▶ aijournal characterize ──▶ proposed profile/claim updates + evidence links
                                                                      ▼
                                                            aijournal review-updates (approve/reject)
```

- **Manifest**: YAML list of `{hash, path, source_type, ingested_at, tags}`, stored under `data/manifest/`. Hashes (SHA‑256) gate ingestion so files are consumed once.
- **Normalization adapters**: pluggable readers (Markdown, HTML, chat export) target the shared `NormalizedEntry` Pydantic model.
- **Characterize**: uses the Pydantic AI model interface to infer traits/motivations/claims from aggregated entries, writing proposed edits with `evidence_hashes`, `method`, `confidence`, and `review_after_days` adjustments.
- **Review**: staged updates land in `derived/pending/profile_updates/<timestamp>.yaml`; `aijournal review-updates --apply` merges them into `profile/self_profile.yaml` and `profile/claims.yaml` once approved.

---

## 2.1 Project Management with uv

Use uv for all Python project management: initialization, dependency resolution, virtualenv, locking, running, and building.

- Prerequisites
  - Install uv (https://docs.astral.sh/uv/) and ensure `uv --version` works.
  - Ensure Python ≥3.11 is available; pin via `requires-python` in `pyproject.toml`.

- Bootstrap (run in repo root)
  - `uv init --package aijournal`  # creates `pyproject.toml` and src layout
  - Edit `pyproject.toml`:
    - `[project] name = "aijournal"`
    - `requires-python = ">=3.11,<3.13"`
    - `[project.scripts] aijournal = "aijournal.cli:app"` (added when CLI exists)
- Add runtime deps:
    - `uv add typer pyyaml httpx pydantic python-dateutil`
  - Add dev/test deps:
    - `uv add -D pytest pytest-cov mypy ruff hypothesis types-PyYAML types-python-dateutil`
  - Lock and verify:
    - `uv lock`
    - `uv run pytest -q` (will pass once tests exist)
  - Commit both `pyproject.toml` and `uv.lock` for reproducibility.

- Daily usage
  - Run any tool inside the project env: `uv run <cmd>` (e.g., `uv run pytest`)
  - Add/remove deps: `uv add ...`, `uv remove ...`
  - Update/lock: `uv lock --upgrade` (or targeted upgrades)
- One-off tools: `uvx ruff` / `uvx mypy` if you prefer ephemeral tools (optional; dev deps already pinned).

## 2.2 Runtime dependencies (add-on for persona-first scope)

Add via `uv add` unless already present:

- `numpy` — vector math for embeddings and consolidation weights.
- `annoy` — approximate nearest neighbor index (portable, rebuildable).
- `fastapi` + `uvicorn` — lightweight local chat server/orchestrator.
- `orjson` — faster JSON (optional but recommended for chat transcripts/index meta).

Embedding defaults rely on Ollama (e.g., `embeddinggemma`); keep everything local-first.

---

## 3. Data Models (Authoritative)

All YAML is UTF‑8, LF line endings, stable key ordering.

### 3.1 Journal Entry (Markdown + frontmatter)
- Path: `data/journal/YYYY/MM/DD/<slug>.md`
- Frontmatter:
  - `id: string` (uuid7 or time‑slug)
  - `created_at: ISO8601Z`
  - `title: string`
  - `tags: [string]`
  - Optional: `mood: string`, `projects: [string]`

Example:

```markdown
---
id: "2025-10-25_x9t3"
created_at: "2025-10-25T09:41:00Z"
title: "Morning notes: schedule + twins"
tags: ["family", "planning"]
mood: "calm"
projects: ["aijournal"]
---
Had a quiet morning. Planned the week. Noticed energy best 9–12...
```

### 3.2 Normalized Entry (machine‑readable mirror)
- Path: `data/normalized/YYYY-MM-DD/<slug>.yaml`

```yaml
id: "2025-10-25_x9t3"
created_at: "2025-10-25T09:41:00Z"
source_path: "data/journal/2025/10/25/morning-notes.md"
title: "Morning notes: schedule + twins"
tags: ["family", "planning"]
entities:
  - type: "person"
    value: "Jess"
sections:
  - heading: "Had a quiet morning"
    para_index: 0
```

### 3.3 Self Profile (facets, provenance, cadence)
- Path: `profile/self_profile.yaml`
- Provenance per field: `method: self_report|inferred|behavioral`, `user_verified: bool`, optional `evidence: [source_ids]`.
- Re‑validation: `review_after_days` at facet or field level.

Seed (drop‑in):

```yaml
traits:
  big_five:
    openness: {score: 0.74, method: self_report, user_verified: true}
    conscientiousness: {score: 0.68, method: inferred}
    extraversion: {score: 0.42, method: self_report}
    agreeableness: {score: 0.61, method: inferred}
    neuroticism: {score: 0.33, method: self_report}
  regulatory_focus: {promotion: 0.7, prevention: 0.3}
  risk_tolerance: {domain: "career", level: "medium-high"}
  time_horizon: {preferred: "long", evidence: ["2024_l2_..."]}
  review_after_days: 180

values_motivations:
  schwartz_top5: ["Self-Direction", "Achievement", "Universalism", "Benevolence", "Security"]
  sdt: {autonomy: 0.8, competence: 0.7, relatedness: 0.6}
  drivers:
    - value: "Mastery over tools & systems"
      method: inferred
      confidence: 0.8
  review_after_days: 120

goals:
  short_term:
    - value: "Ship personal agent MVP"
      why: "reduce friction"
      krs: ["CLI usable", "context pack <1800t"]
      review_after_days: 30
  long_term:
    - value: "Work-life consistency with twins"
      krs: ["2 evenings/week protected"]
      review_after_days: 90
  anti_goals:
    - value: "No late-night production firefighting as a norm"
      reason: "family/health"

decision_style:
  default: {speed_vs_quality: "quality", satisficer_vs_maximizer: "bounded_maximizer"}
  implementation_intentions:
    - if: "Feeling anxious before presentations"
      then: "Run checklist + 10-min rehearsal"
      evidence: ["2021-04-12_l1"]

affect_energy:
  energy_map: {morning: "high", afternoon: "medium", evening: "low"}
  stressors: ["ambiguous deadlines", "noisy environment"]
  coping_strategies: ["walks", "time-boxing", "no email after 18:00"]

social:
  relationships:
    - person: "Jess"
      role: "coworker"
      notes: "great feedback partner"
      boundary: "no pings after 18:00"

boundaries_ethics:
  red_lines: ["No sharing private family data", "No health advice beyond guidelines"]

coaching_prefs:
  tone: "direct, warm"
  depth: "concrete first, theory second"
  probing: {max_questions: 2, prefer: "yes/no + one short open follow-up"}
```

### 3.4 Claims (evidence-linked)
- Path: `profile/claims.yaml`

```yaml
claims:
  - id: "pref_deep_work_morning"
    statement: "Best deep work between 09:00–12:00."
    status: "accepted"          # accepted|tentative|rejected
    confidence: 0.78
    freshness: 0.92             # 0..1 derived from staleness algorithm
    sources:
      - entry_id: "2025-10-25_x9t3"
        spans:
          - {type: "para", index: 0}
    method: "inferred"
    user_verified: true
    review_after_days: 120
    last_updated: "2025-10-25T10:10:00Z"
```

### 3.5 Claim atoms with scope (persona-first upgrade)

- Path stays `profile/claims.yaml`, but each entry is a **ClaimAtom** with explicit type, subject, predicate, value, and scope. Reference implementation lives in `src/aijournal/models/claims.py`.
- Scope captures `domain`, `context[]`, and `conditions[]` so advisors/interviews can differentiate weekday vs weekend, solos vs teams, etc.
- Provenance is normalized (`sources[]`, `first_seen`, `last_updated`).

Example:

```yaml
claims:
  - id: "pref.deep_work.window"
    type: "preference"
    subject: "deep_work"
    predicate: "best_window"
    value: "09:00-12:00"
    statement: "Best deep work between 09:00–12:00 on weekdays."
    scope: {domain: "work", context: ["weekday"], conditions: []}
    strength: 0.78
    status: "accepted"
    method: "inferred"
    user_verified: true
    review_after_days: 120
    provenance:
      sources:
        - entry_id: "2025-10-25_x9t3"
          spans: [{type: "para", index: 0}]
      first_seen: "2024-11-02"
      last_updated: "2025-10-25T10:10:00Z"
```

Add `aijournal migrate claims-v0.2-to-atoms` to convert legacy free-form claims into atoms; unclear parses become `status: tentative` and require review.

---

## 4. Data Models (Derived)

Derived artifacts include immutable metadata: `llm_model`, `prompt_path`, `prompt_hash`, `created_at`.

### 4.1 Day Summary
- Path: `derived/summaries/YYYY-MM-DD.yaml`

```yaml
day: "2025-10-25"
bullets:
  - "Planned week; morning energy was high."
highlights:
  - "Family scheduling sorted."
todo_candidates:
  - "Block two evenings for twins."
meta:
  llm_model: "llama3.1:8b-instruct"
  prompt_path: "prompts/summarize_day.md"
  prompt_hash: "sha256:..."
  created_at: "2025-10-25T11:00:00Z"
```

### 4.2 Micro‑Facts
- Path: `derived/microfacts/YYYY-MM-DD.yaml`

```yaml
facts:
  - id: "deep_work_morning"
    statement: "Morning is best for deep work."
    confidence: 0.72
    evidence:
      entry_id: "2025-10-25_x9t3"
      spans: [{type: "para", index: 0}]
    first_seen: "2025-10-25"
    last_seen: "2025-10-25"
meta: {llm_model: "...", prompt_path: "...", prompt_hash: "...", created_at: "..."}
```

### 4.3 Profile Suggestions (facets + claims)
- Path: `derived/profile_suggestions/YYYY-MM-DD.yaml`
- Defaults to `user_verified: false`.

```yaml
upserts:
  - target: "claims"
    operation: "upsert"
    value:
      id: "pref_deep_work_morning"
      statement: "Best deep work between 09:00–12:00."
      status: "tentative"
      confidence: 0.7
      freshness: 1.0
      sources: [{entry_id: "2025-10-25_x9t3"}]
      method: "inferred"
      user_verified: false
      review_after_days: 120
    rationale: "Repeated mention of high morning energy."
updates:
  - target: "self_profile.traits.time_horizon.preferred"
    operation: "set"
    value: "long"
    method: "inferred"
    user_verified: false
    evidence: ["2024_l2_..."]
    rationale: "Emphasis on multi‑quarter outcomes."
meta: {llm_model: "...", prompt_path: "...", prompt_hash: "...", created_at: "..."}
```

### 4.4 Interview Questions
- Path: `derived/interviews/YYYY-MM-DD.yaml`
- Prioritization: staleness × impact weighting; falls back to the 8 high‑impact probes when gaps exist.

```yaml
questions:
  - id: "q_values_rank"
    text: "Top 3 values you refuse to trade off—rank them."
    target_facet: "values_motivations.schwartz_top5"
    priority: "high"
  - id: "q_deep_work_window"
    text: "Energy map: when are you best for deep work vs admin?"
    target_facet: "affect_energy.energy_map"
    priority: "high"
meta: {llm_model: "...", prompt_path: "prompts/profile_probe.md", prompt_hash: "...", created_at: "..."}
```

### 4.5 Advice Card (Advisor Mode output)
- Path: `derived/advice/YYYY-MM-DD/<id>.yaml`
- An immutable record of personalized advice with explicit links back to your profile facets and claims.

```yaml
id: "adv_2025-10-25_01"
query: "How should I schedule my week to protect family time while shipping the MVP?"
assumptions:
  - "You prefer deep work 09:00–12:00 (claims.pref_deep_work_morning)."
  - "Top values include Self‑Direction and Security (self_profile.values_motivations)."
  - "Anti‑goal: avoid late‑night firefighting (self_profile.goals.anti_goals)."
recommendations:
  - title: "Block two deep‑work mornings (Mon/Wed)"
    why_this_fits_you:
      facets: ["affect_energy.energy_map", "goals.short_term", "values_motivations.schwartz_top5"]
      claims: ["pref_deep_work_morning"]
    steps: [
      "Create calendar blocks 09:00–12:00 Mon/Wed.",
      "Route admin to 15:00–16:30 Tue/Thu.",
      "Add 18:00 shutdown checklist to avoid spillover." ]
    risks: ["Unexpected work pings"]
    mitigations: ["Set Slack status after 18:00; escalate only for P0."]
  - title: "Protect two evenings for family (Tue/Fri)"
    why_this_fits_you:
      facets: ["goals.long_term", "boundaries_ethics.red_lines"]
      claims: []
    steps: ["Recurring 17:30–20:30 family block; phone on DND."]
    risks: ["Release crunch"]
    mitigations: ["Move release prep to morning deep‑work windows."]
tradeoffs:
  - "Shipping speed may dip slightly; quality and sustainability improve."
next_actions: [
  "Add four recurring blocks (Mon/Wed AM deep work; Tue/Fri PM family).",
  "Create a shutdown checklist reminder at 17:50."]
confidence: 0.72
alignment:
  values: ["Self-Direction", "Security"]
  goals: ["Ship personal agent MVP", "Work-life consistency with twins"]
style:
  tone: "direct, warm"
  depth: "concrete-first"
meta:
  llm_model: "llama3.1:8b-instruct"
  prompt_path: "prompts/advise.md"
  prompt_hash: "sha256:..."
  created_at: "2025-10-25T12:00:00Z"
  safety:
    respected_red_lines: true
    filtered_topics: []
```

### 4.6 Profile Update Batch (pending review)
- Path: `derived/pending/profile_updates/<timestamp>.yaml`
- Captures claim/facet proposals plus manifest hashes before human approval.

```yaml
batch_id: "2025-02-03-2025-02-03T12:00:00Z"
created_at: "2025-02-03T12:00:00Z"
date: "2025-02-03"
inputs:
  - id: "2025-02-03-focus-notes"
    normalized_path: "data/normalized/2025-02-03/2025-02-03-focus-notes.yaml"
    source_hash: "abc123"
    manifest_hash: "abc123"
    tags: ["focus", "planning"]
proposals:
  claims:
    - claim:
        id: "focus-theme-claim"
        statement: "Focus planning remains a recurring priority."
        status: "tentative"
        confidence: 0.62
        method: "inferred"
        review_after_days: 120
        user_verified: false
        sources:
          - entry_id: "2025-02-03-focus-notes"
            spans: []
      normalized_ids: ["2025-02-03-focus-notes"]
      evidence_hashes: ["abc123"]
      manifest_hashes: ["abc123"]
      rationale: "Daily notes emphasize focus blocks."
  facets:
    - path: "values_motivations.recurring_theme"
      operation: "set"
      value:
        label: "Morning focus"
        tag_hint: "focus"
      method: "inferred"
      confidence: 0.55
      review_after_days: 90
      user_verified: false
      normalized_ids: ["2025-02-03-focus-notes"]
      evidence_hashes: ["abc123"]
      rationale: "Morning session repeated twice."
meta:
  llm_model: "llama3.1:8b-instruct"
  prompt_path: "prompts/characterize.md"
  prompt_hash: "sha256:..."
  created_at: "2025-02-03T12:00:00Z"
```

### 4.7 Retrieval index artifacts

- SQLite FTS database: `derived/index/index.db` stores chunk metadata + `fts5` virtual table.
- Annoy vector file: `derived/index/annoy.index` stores embeddings keyed by SQLite rowid.
- `derived/index/meta.json` captures embedding model, vector dim, build time, count, Annoy params, and whether fake mode was used.
- Chunk schema: `{id, normalized_id, date, tags, source_type, chunk_index, chunk_text, tokens}` with deterministic chunking (700–1200 chars, sentence boundaries, include section headings when available).
- Rebuild commands regenerate both indexes deterministically from normalized YAML; no authoritative data stored here.
- **Chunk manifests for inspection:** deterministic YAML manifests under `derived/index/chunks/YYYY-MM-DD.yaml` (plus optional `.npy` vector shards) mirror the indexed chunks so humans can audit or feed them to other tools, but the primary retrieval path still depends on the SQLite/Annoy artifacts.
- **Operator search surface:** `aijournal index search "query" --tags focus --source journal --date-from 2025-02-01` wraps the shared Retriever with formatted streaming output, enforcing the same Annoy + FTS5 prerequisites and filter semantics that downstream chat relies on.

### 4.8 Persona core file (L1)

- Path: `derived/persona/persona_core.yaml`.
- Trigger: regenerate whenever `profile/*.yaml` or `claims.yaml` changes, or when `persona build` is invoked explicitly.
- Contents: values/goals/boundaries/coaching prefs snapshot plus top-N accepted claim atoms ranked by `effective_strength × impact_weight` and trimmed to ≤ ~1200 tokens.
- Metadata: include `{generated_at, llm_model (if any), selection_strategy, trimmed}` for determinism.

### 4.9 Claim consolidation & conflict handling

- Implement `ClaimConsolidator` service that ingests new micro-facts/characterization outputs and merges them into ClaimAtoms using weighted averaging:
  - `strength_new = clamp01((w_prev * strength_prev + w_obs * signal) / (w_prev + w_obs))`
  - `w_prev = min(1.0, log1p(n_prev))`, `w_obs = 1.0`, `signal = evidence confidence (default 0.6)`.
- Conflicts (same `type/subject/predicate/scope` but different `value`):
  - If qualifiers exist (weekend vs weekday, solo vs team) split into scoped atoms.
  - Otherwise downgrade both to `status: tentative`, drop strengths by 0.15, and enqueue an interview question.
- Decay applied at read time only: `effective_strength = strength * exp(-lambda * staleness)` with `lambda ≈ 0.2` and `staleness = min(2, days_since / review_after_days)`.
- `review-updates` must surface conflicts + scopes so humans understand why merges occur before applying.
- **Status (2025-02-03):** CLI surfaces typed responses for `facts`, `summarize`, `profile suggest`, and `characterize` via Pydantic AI response models. Retrying schema failures is now configurable (`--retries`) and progress logging is exposed with `--progress`. Consolidation previews continue to include conflict scopes and interview prompts.

---

## 5. IDs, Slugs, and Time

- IDs: `uuid7` or `YYYY-MM-DD_<shortid>` for human scanability.
- Slugs: lowercase, `a-z0-9-`, collapse whitespace, strip punctuation.
- Time: store in UTC ISO8601 with `Z`.
- Path mapping is deterministic: `id -> source_path` and `date -> YYYY/MM/DD`.

---

## 6. Provenance and Re‑Validation

- Every facet/claim stores `method`, `user_verified`, optional `evidence`.
- Re‑validation cadence per facet/field: `review_after_days`.
- Staleness score: `staleness = min(2.0, days_since_last_updated / review_after_days)`.
- Impact weights (defaults; configurable):
  - values/goals: 1.5
  - decision_style: 1.3
  - affect_energy: 1.2
  - traits: 1.0
  - social: 0.9
- Interview ranking: `rank = staleness × impact_weight`. Pick top 2–4.
- Advisor uses the same ranking to focus recommendations on high‑impact areas.

---

## 7. Hierarchical Memory (L1→L4)

- **L1 (Persona Core):** `derived/persona/persona_core.yaml` + top accepted claim atoms; always included in packs/chat.
- **L2 (Recent Activity):** today’s normalized entries plus last 7 days of summaries + high-confidence micro-facts (≤ ~900 tokens by trimming).
- **L3 (Extended Profile):** full accepted claims + extended self_profile facets (deterministically trimmed, ≤ ~1800 tokens).
- **L4 (Background):** prompts, config, and raw journal files for the base day ± optional `--history-days`.

Command: `aijournal pack --level L3 --out /tmp/context.txt` (uses standardized trimming metadata described in §2.2).

---

## 8. Ollama Integration

- Default endpoint: `http://localhost:11434`.
- Models: `llama3.1:8b-instruct` (configurable).
- Client: thin wrapper with `generate(prompt:str, json_schema?:dict) -> str|dict`.
- Deterministic tests: `AIJOURNAL_FAKE_OLLAMA=1` to use local fixtures.
- Metadata stamped into all derived files: `llm_model`, `prompt_path`, `prompt_hash`, `created_at`.

Health check:
- `aijournal ollama health` returns model list and selected default.

### 8.1 Embedding + retrieval services

- Embeddings default to `embeddinggemma` via Ollama (`EmbeddingClient` wraps HTTP calls, caches dims, exposes `embed_texts`).
- `Indexer` manages chunking (700–1200 chars, boundary aware), writes SQLite rows, and updates Annoy (rebuild after N inserts or on-demand `index rebuild`).
- `Retriever` loads query embeddings, performs ANN search (`search_k = factor * k * trees`), filters by tags/date/source, applies lightweight rerank: `score = 0.7*cos + 0.3*recency` where `recency = 1/(1+0.05*days)`.
- Retrieval requires the Annoy + SQLite artifacts; if either is missing, commands error immediately so you can rebuild via `aijournal index rebuild`.
- CLI operators now have `aijournal index search` for local QA/debug: it reuses `Retriever.search`, respects fake mode, exits non-zero when artifacts are missing, and prints scored snippets with `{date, source_path, tags}` metadata to keep expectations aligned with upcoming chat usage.

### 8.2 Chat orchestrator (RAG + write-back)

- `aijournal chat` (CLI/TUI) and `aijournal chatd` (FastAPI) wrap a pipeline:
  1. Maintain rolling summaries under `derived/chat_sessions/<id>/summary.yaml` when history grows.
  2. Intent classify each user turn (`advice|planning|reflection|qa_about_me|meta`).
  3. Retrieve relevant claims (filtered by intent/subject heuristics + top `effective_strength`) and journal chunks via `Retriever.search` (k≈12, filters optional).
  4. Assemble context: persona core (L1), selected claims, retrieved chunks (with citations), conversation summary, and any relevant config/coach prefs respecting token budget + deterministic trimming metadata.
  5. Generate responses that must cite claims/journal entries (`[claim:pref.deep_work.window]`, `[entry:2025-10-25_x9t3#p0]`), include `why this fits you`, and ask at most one clarifying question if `coaching_prefs.probing` allows.
  6. Extract micro-facts from user messages, run them through `ClaimConsolidator`, and queue updates under `derived/pending/profile_updates/…` (or micro-facts) with explicit provenance.
  7. Capture feedback (thumbs up/down) to tweak cited claim strengths (+0.03/−0.05 within 0..1).
- Outputs live under `derived/chat_sessions/<session_id>/{transcript.json, summary.yaml, learnings.yaml}` with deterministic ordering + metadata.

---

## 9. Configuration

- Path: `config/config.yaml`
  - `model: "llama3.1:8b-instruct"`
  - `embedding_model: "embeddinggemma"`
  - `temperature: 0.2`
  - `seed: 42`
  - `paths: {data, profile, derived, prompts}`
  - `impact_weights: {...}` (extend to include claim types: value, goal, boundary, trait, preference, habit, skill)
  - `advisor: {max_recos: 3, include_risks: true}`
  - `chat: {max_retrieved_chunks: 12, max_claims: 16, follow_up_enabled: true, write_back_facts: true}`
  - `index: {rebuild_threshold: 1000, ann_trees: 50, search_k_factor: 3}`
  - `token_estimator: {char_per_token: 4.2}`
- Env overrides:
  - `AIJOURNAL_CONFIG=...`
  - `AIJOURNAL_FAKE_OLLAMA=1`
  - `AIJOURNAL_MODEL=...` (overrides config model)

---

## 10. CLI Surface

- `aijournal init` — create directories, seed config and example prompts; idempotent.
- `aijournal new "Title"` — create Markdown entry with frontmatter.
- `aijournal normalize --date YYYY-MM-DD` — MD→normalized YAML (no LLM).
- `aijournal summarize --date YYYY-MM-DD` — day summary via Ollama.
- `aijournal facts --date YYYY-MM-DD` — extract micro‑facts via Ollama.
- `aijournal profile status` — list stale/high-impact facets/claims with ranks.
- `aijournal profile suggest [--since YYYY-MM-DD]` — write suggestions YAML (facets+claims).
- `aijournal profile apply [--file derived/profile_suggestions/...]` — interactive accept/merge.
- `aijournal characterize --date YYYY-MM-DD` — emit manifest-linked claim/facet proposals under `derived/pending/profile_updates/`.
- `aijournal review-updates [--file ...] [--apply]` — inspect pending batches, preview scope conflicts, and optionally merge them into `profile/`.
- `aijournal interview --max 4` — prioritized probes (information-gain scoring, scope-focused questions).
- `aijournal advise "question" [--level L1|L2|L3] [--max 3]` — Advisor Mode; generates `derived/advice/...yaml` with citations.
- `aijournal pack --level L1|L2|L3|L4 --out path` — assemble context pack with standardized layer semantics + trimming metadata.
- `aijournal persona build` — regenerate `derived/persona/persona_core.yaml` (feeds packs + chat primer).
- `aijournal index rebuild` — rebuild SQLite + Annoy from normalized YAML.
- `aijournal index tail` — tail manifest and incrementally index new normalized files.
- `aijournal chat` — local RAG chat (retrieval, citations, single clarifying question, learnings write-back).
- `aijournal chatd --port <int>` — FastAPI server exposing the same orchestrator.
- `aijournal ollama health` — verify local model availability (fake mode warns when falling back).

Interactive apply (text-mode):
- Show each suggestion diff (YAML delta) plus scope/conflict notes, accept/skip, then write back to authoritative file(s) and update timestamps/freshness.

---

## 11. Prompts

All prompts are files under `prompts/`. Each call records `prompt_hash = sha256(file_contents)`.

- `summarize_day.md`: concise bullets, highlights, and TODOS with JSON‑like structure.
- `extract_facts.md`: instruction to propose atomic statements with evidence locators referencing normalized entry IDs and spans.
- `profile_suggest.md`: propose deltas for `claims.yaml` and `self_profile.yaml`, always include `method`, default `user_verified: false`, and `review_after_days` suggestions.
- `profile_probe.md`: synthesize 2–4 targeted questions. If missing/low‑verified facets exist, include the “8 high‑impact probes”.
- `advise.md`: produce concrete recommendations constrained by `coaching_prefs` and `boundaries_ethics`. Must:
  - cite linked facets/claims in `why_this_fits_you`
  - include risks/mitigations when relevant
  - avoid restricted topics per `boundaries_ethics`
  - use tone/depth from `coaching_prefs`

---

## 12. Evidence Locators

To make evidence robust yet simple:
- `spans` are a list of locators:
  - `{type:"para", index:int}` or `{type:"heading", text:"..."}`
  - Optional char offsets: `{type:"char", start:int, end:int}` only if needed.
- Locators are validated against the current normalized entry.

---

## 13. Testing Strategy

Unit:
- Pydantic (de)serialization for JournalEntry, NormalizedEntry, MicroFact, ClaimAtom + Scope/Provenance, SelfProfile facets.
- Claim consolidation math (aggregation, contradiction handling, decay-at-read helper).
- Chunker determinism (same text → same chunk boundaries) + token estimator.
- Retrieval rerank scores (cosine + recency) and filter logic.
- Path mappers and slug/ID generators are deterministic.
- Staleness/impact/uncertainty scoring for interviews.

Functional (CLI):
- `init/new/normalize` produces expected files and paths.
- `summarize/facts` under fake mode write valid derived YAML; validate against Pydantic models; snapshot key sections.
- `advise` under fake mode returns a valid Advice Card and respects tone/boundaries.
- `index rebuild` + `index tail` build reproducible SQLite/Annoy artifacts; `retriever.search` returns stable top-K on seed corpus.
- `persona build` creates deterministic persona_core ≤ token budget.
- `chat` end-to-end: retrieves claims + chunks, enforces citations, stores transcript/summary/learnings, and writes pending updates when write-back enabled.
- `review-updates --apply` shows conflict scope info before merging.
- Interview ranking favors high uncertainty/missing scope facets.

Validation:
- Pydantic models cover each artifact (`DailySummary`, `MicroFactsFile`, `AdviceCard`, `ClaimAtom`, `PersonaCore`, `ProfileUpdateBatch`, etc.).
- `pytest` loads written YAML and validates via those models.

LLM Contracts:
- Golden fixtures in `tests/fixtures/ollama/`.
- Changing prompts updates `prompt_hash`; tests ensure fixture refresh is required.

Static:
- `mypy` for type hints.
- Optional `ruff` for lint.

Fixtures:
- Fake journal MD and normalized YAML.
- Seed `self_profile.yaml` and a minimal `claims.yaml`.
- Example advice fixtures covering scheduling, prioritization, decision trade‑offs.

Coverage:
- Aim for 80%+ on core modules.

---

## 14. Fake Data Generation

- `aijournal new --fake N` generates N entries with plausible frontmatter + body text (no LLM).
- Include `--seed <int>` for deterministic fixtures (CI/tests) or omit to anchor on "now".
- Optional `--tags` overrides the auto-tagged values so demos can highlight specific themes.

---

## 15. Reproducibility

- All derived artifacts have `meta` with model, prompt path, prompt hash, created time.
- Optional `config/models.lock.yaml` to pin model digest/version if available from Ollama.
- `aijournal rebuild --since YYYY-MM-DD` deletes and regenerates derived artifacts deterministically (given same prompts/model/config and fake mode off).

---

## 16. Logging and Errors

- Human‑readable INFO logs to stderr; record each file written.
- Errors include actionable hints (e.g., “No normalized entries for date …”).
- `--verbose` flag for HTTP traces to Ollama.

---

## 17. Security and Privacy

- No network I/O besides localhost Ollama by default.
- `.gitignore` may exclude `derived/` if you prefer not to commit artifacts.
- Prompts and configs are safe to commit; personal data in `data/` and `profile/` is at user discretion.
- Advisor enforces `boundaries_ethics.red_lines` and filters health/finance/medical/legal advice to “general guidance only” with professional disclaimers.

---

## 18. Performance Notes

- Journals are small; YAML loads are fast.
- If needed later: caching prompt outputs keyed by `(model, prompt_hash, inputs_hash)` under `derived/cache/`.

---

## 19. Implementation Details

Language/Runtime:
- Python 3.11+
- Dependencies (runtime): `typer`, `PyYAML`, `httpx`, `pydantic`, `python-dateutil`
- Dev: `pytest`, `pytest-cov`, `mypy`, `ruff` (optional), `hypothesis` (optional)

Conventions:
- Pydantic models handle structure/validation while YAML stays human-friendly.
- Stable YAML dump (sorted keys); keep nulls out unless required.
- Small pure functions; avoid global state; pass config explicitly.

IDs:
- Use uuid7 or time‑slug generator; include deterministic short suffix.

Freshness:
- `freshness` in claims derived as `1.0 - min(1.0, days_since / review_after_days)` at read time; stored value can be updated on write for convenience.

---

## 20. justfile (Tasks)

```
test:        uv run pytest -q
test_cov:    uv run pytest --cov=src -q
mypy:        uv run mypy src
lint:        uv run ruff check src tests
fmt:         uv run ruff format src tests
health:      uv run aijournal ollama health
fake_on:     export AIJOURNAL_FAKE_OLLAMA=1
ci:          just fake_on test mypy
```

---

## 21. Stepwise Commit Plan (Small, Frequent)

1) chore(init): uv bootstrap + skeleton
- Initialize project with uv: `uv init --package aijournal`.
- Edit `pyproject.toml` to set project metadata and `requires-python`.
- Add `.gitignore`, `README.md`, `PLAN.md`, `justfile`, `config/config.yaml`.
- Add empty `profile/claims.yaml` and seed `profile/self_profile.yaml` with the provided YAML.
- Commit `pyproject.toml` and `uv.lock`.
- Tests folder scaffold.

2) feat(cli): init
- Implement idempotent directory creation with clear output.
- Tests: initializing twice is no‑op and returns success.

3) feat(core): models + validation
- Pydantic models for journal, normalized, summary, micro-facts, claim, self profile facets, suggestions, interviews, advice.
- Validation helpers wrapping those models.
- Tests: (de)serialization + Pydantic validation round-trips.

4) feat(cli): new
- Create MD with frontmatter; deterministic slug/id.
- Tests: frontmatter correctness; path mapping.

5) feat(core): normalize
- Parse frontmatter and headings to normalized YAML (no LLM).
- Tests: stable YAML keys; entities extraction stub.

6) feat(ollama): client + fake mode
- `OllamaClient` (generate), health check, env fake path.
- Add runtime deps with uv if missing: `uv add httpx`.
- Tests: fake mode fixtures; health check handles no Ollama gracefully.

7) feat(derive): summarize
- Prompt call → `derived/summaries/DATE.yaml` with meta.
- Tests: Pydantic validation + snapshot of bullets.

8) feat(derive): facts
- Prompt call → `derived/microfacts/DATE.yaml` with evidence locators.
- Tests: Pydantic validation + evidence linking to normalized entry id.

9) feat(profile): ranking + status
- Implement staleness and impact ranks.
- `aijournal profile status` summarizes top stale/high‑impact facets and claims.
- Tests: deterministic ranking.

10) feat(profile): suggest
- Aggregate micro-facts and diff `self_profile.yaml` + `claims.yaml` → suggestions YAML.
- Tests: default `user_verified=false`, `method` present, model validation passes.

11) feat(cli): apply suggestions
- Interactive accept/reject; write authoritative files; update timestamps/freshness.
- Tests: merging preserves evidence; no duplicate claim IDs.

12) feat(interview): prioritized probes
- Generate 2–4 questions using ranking; fall back to 8 high‑impact probes.
- Tests: questions reference facet keys or claim IDs; priorities set.

13) feat(advice): Advisor Mode (advise)
- Implement `aijournal advise` that loads L3 context + recent L2, obeys `coaching_prefs` and `boundaries_ethics`, emits Advice Card YAML and prints a concise summary.
- Tests: respects tone, cites linked facets/claims, includes risks/mitigations when relevant, adheres to red lines.

14) docs: refine README and examples
- Include end‑to‑end usage, fake mode, regeneration semantics, Advisor Mode examples.

15) optional: pack
- `aijournal pack --level L1|L2|L3|L4` assembles context; token-aware trimming.

### Addendum roadmap (persona-first scope)

16) feat(models): typed ClaimAtom + Scope + Provenance (breaking change allowed; reinit data).

17) feat(index): SQLite + Annoy indexer (`index rebuild`, `index tail`) + deterministic retrieval tests.

18) feat(persona): persona_core builder + updated L1 packs (token budgeting + trimmed metadata).

19) feat(consolidation): merge/conflict/decay utilities wired into `characterize`, `profile suggest/apply`.

20) feat(chat): chat orchestrator (CLI + FastAPI) with RAG, citations, write-back learnings.

21) feat(interview+advisor): information-gain ranking, scope-aware prompts, conflict surfacing in `review-updates`.

22) docs: README/PLAN refresh (this doc), config additions, glossary updates.

---

## 22. Acceptance Criteria (persona-first MVP)

- **Persona core coverage:** `derived/persona/persona_core.yaml` includes top values, current goals, boundaries, coaching prefs, and ≥10 high-strength claim atoms with scopes under ≤ ~1200 tokens.
- **Typed claims & consolidation:** Claim atoms include `type/subject/predicate/value/scope`, merge new micro-facts deterministically, and surface conflicts (scoped split or tentative downgrade + interview question).
- **Retrieval relevance:** `aijournal chat` answers reference claims or journal entries with citations ≥90% of the time on seed QA, and retrieval latency stays <150 ms on a laptop with 50k+ chunks.
- **Learning loop:** Marking assistant replies helpful/ unhelpful updates cited claim strengths (+/-) and queues learnings into pending profile updates.
- **Interview upgrade:** Ranking considers staleness, uncertainty, and missing scopes; prompts explicitly probe for qualifiers.
- **Packs & token budgets:** All pack levels include standardized layer semantics, trimming metadata, and remain within configured token budgets.

---

## 23. Glossary

- Authoritative: files edited by the user that define truth (`data/`, `profile/`, `prompts/`, `config/`).
- Derived: reproducible files created from authoritative inputs (`derived/`).
- Facet: a structured field in `self_profile.yaml` (e.g., `traits.big_five.openness`).
- Claim Atom: typed/scoped statement (`type/subject/predicate/value`) with strength + provenance in `claims.yaml`.
- Advice Card: a reproducible artifact with tailored recommendations and traceable rationale.
- Persona Core: deterministic L1 context built from profile + top claim atoms.

---

## 24. Quick Start (once implemented)

- `aijournal init`
- `aijournal new "Kickoff notes"`
- Edit the file; then:
- `aijournal normalize --date 2025-10-25`
- `aijournal summarize --date 2025-10-25`
- `aijournal facts --date 2025-10-25`
- `aijournal profile suggest --since 2025-10-01`
- `aijournal profile apply`
- `aijournal interview --max 4`
- `aijournal advise "How should I schedule next week around family time while shipping the MVP?"`
- `aijournal index rebuild`
- `aijournal persona build`

---

## 25. Execution Roadmap (October 2025)

As of 2025-10-29 the CLI ships `init`, `new`, `ingest`, `normalize`, `summarize`, `facts`,
`profile suggest/apply`, `characterize`, `review-updates`, `advise`, `profile-status`,
`interview`, `pack`, `ollama health`, `index rebuild/tail`, `retriever`, `persona build`, `chat`,
and `chatd`. Chat now persists transcripts/summary/learnings, supports session reuse, emits JSON
telemetry, and `chatd` exposes the same loop via FastAPI. The roadmap below keeps consolidation,
documentation parity, and live-mode polish moving without blocking on LLM availability.

### Immediate focus (week of 2025-10-27)

1. **feat(index): `index rebuild` + `index tail`.** ✅ _Shipped 2025-10-25 via the new Typer group, chunk artifacts (ArtifactKind.INDEX_CHUNKS), Annoy map commits, and CLI coverage in `tests/test_cli_index.py`._
   - Implement chunker → SQLite (`fts5`) → Annoy writer with deterministic ordering.
   - Add `derived/index/meta.json` capturing `{embedding_model, dim, build_time, mode}` plus
     trimmed chunk stats.
   - Expose `--since`/`--limit` knobs and write regression tests that rebuild from
     fixtures and assert identical Annoy/SQLite digests across runs.
   - Write human-readable chunk artifacts (`derived/index/chunks/YYYY-MM-DD.yaml` + optional
     `.npy` vectors) alongside the database artifacts for inspection.

2. **feat(retriever): shared search service.** ✅ _Shipped 2025-10-25 via `aijournal.services.retriever.Retriever`, the shared `EmbeddingBackend`, Pytests covering the ANN search path (`tests/test_retriever.py`), and (2025-10-27) the operator-facing `aijournal index search` CLI with coverage in `tests/test_cli_index.py`._
   - Build `Retriever` API that loads metadata, performs ANN search with `search_k_factor`
     defaults, mixes cosine + recency scoring, and surfaces filter hooks (tags, date, source).
   - Retrieval now requires the generated SQLite + Annoy artifacts; when they are missing, `Retriever.search`
     raises a clear error directing operators to rebuild the index.
   - Ship Pytest coverage that stubs embeddings, verifies deterministic ranking, and asserts loud failures when the index is absent.

3. **chore(pack + docs): token math + recent-history parity.** ✅ _Shipped 2025-10-26 by aligning pack token math with the shared estimator and documenting the updated L2 scope._
   - `pack` now reuses the shared `_token_estimate` helper (respecting `token_estimator.char_per_token`)
     so persona/index/pack trimming rules stay consistent.
   - L2 packs include the anchor day’s normalized entries plus the latest seven summaries/micro-facts,
     matching the README promise.
   - README updated with the new token-estimator note; fake `ollama health` refresh tracked separately.

### Next focus (week of 2025-11-03)

4. **feat(persona-core): `persona build` + auto-regeneration.** ✅ _Shipped 2025-10-26 via the new `aijournal persona build` command, schema-backed payloads, trimming metadata, Pytests, and now (2025-10-26) mtime-based reminders through `aijournal persona status` + pack gating that require fresh `derived/persona/persona_core.yaml`._
   - Compose persona core from `self_profile` + accepted claim atoms sorted by
     `effective_strength × impact_weight`, trim to ≤1200 tokens, and capture `meta` with
     generator metadata.
   - Integrate with packs: L1 always references the latest persona core; L2/L3 reuse it when
     assembling bundles so context stays deterministic.
   - Change reminders now run through `persona status` (compares mtimes) and `pack` prints a
     warning whenever the cached persona core is stale.

5. **feat(consolidation): pending-update fusion.**
   - Integrate the new `ClaimConsolidator` with micro-facts/characterize pipelines and queue follow-up
     interviews whenever conflicts are detected (status drops to `tentative`). _Characterize batches now
     emit consolidation previews + queued interview prompts (2025-10-26); micro-facts ingest gained the
     same preview + prompt wiring 2025-10-27._
   - Flesh out scope-splitting heuristics (weekday vs. weekend, solo vs. team) instead of only
     downgrading strength when values differ. _Implemented 2025-10-27 with automatic scope splits before
     falling back to tentative downgrades._
   - Extend regression coverage: conflicting scope handling, decay math, manifest/evidence propagation,
     and interview prompt generation. _New CLI micro-facts + consolidator tests landed 2025-10-27._

### Later focus (mid-November 2025)

6. **feat(chat + chatd): retrieval-backed conversation loop.** ✅ _Shipped 2025-10-29 with session-aware transcripts, clarifying questions, FastAPI streaming, and telemetry fixtures._
   - CLI `aijournal chat` now supports `--session/--save` for appendable transcripts under
     `derived/chat_sessions/<session>/`, emits structured telemetry (`event: chat.telemetry`), and
     accepts `--feedback up|down` to nudge cited claims while queuing feedback batches.
   - Live runs surface at most one clarifying question based on `coaching_prefs.probing` and respect
     fake mode for deterministic CI runs. Tests cover session persistence, feedback nudges, and the
     new options.
   - FastAPI `aijournal chatd` mirrors the orchestrator, streaming NDJSON frames, persisting
     transcripts when requested, and sharing the feedback/telemetry plumbing across CLI + API
     surfaces.

7. **feat(feedback + telemetry): learnings + strength nudges.** ✅ _Shipped 2025-10-29 alongside the chat updates._
   - `--feedback up|down` adjusts cited claim strengths (+0.03 / −0.05 clamped 0..1), logs the delta,
     and records learnings in both the chat session bundle and
     `derived/pending/profile_updates/feedback_*.yaml`.
   - Chat and pack now emit JSON telemetry summarising retrieval latency, chunk counts, and token
     budgets so regressions are script-friendly without extra tooling.

8. **feat(profile-structured-live): align prompts with live Ollama responses.** ✅ _Shipped 2025-10-29 via prompt/schema refresh and interview orchestration._
   - `prompts/characterize.md` now matches the `CharacterizeResponse` schema (nested `claim`
     payloads, provenance arrays, interview prompts). Live runs validate successfully instead of
     dropping back to heuristics, with new Pytests covering the structured path.
   - `aijournal interview` uses the shared structured LLM helper when live mode is available,
     honouring `coaching_prefs.probing.max_questions` and falling back gracefully to heuristics in
     fake mode. Tests cover both fake and live-mode flows.

9. **feat(interview+advisor): information-gain ranking & scope-aware prompts.** ✅ _Shipped 2025-10-29 with richer interview targeting and advisor alignment._
   - Interview ranking now blends staleness, strength, claim status, scope coverage, and recent
     entry tags to prioritise high-impact follow-ups, while pending review prompts are surfaced as
     top-priority questions.
   - CLI fallback questions reference the new metadata (missing contexts, reasons), and the live LLM
     prompt (`prompts/interview.md`) receives the structured payload (`kind`, `reasons`,
     `missing_context`) so generated probes stay scope-aware.
   - Advisor Mode also ingests the same ranking + pending prompts, weaving top information gaps into
     assumptions, steps, and alignment metadata.

10. **docs: README/PLAN refresh + glossary alignment.** ✅ _Shipped 2025-10-29 with chat/advisor documentation updates._
   - README now documents chat session persistence, feedback nudges, telemetry logging, FastAPI `chatd`
     streaming usage, and Advisor Mode’s new ranking insights.
   - PLAN.md and CHANGELOG.md were refreshed so roadmap status matches shipped surfaces, keeping
     engineering and operator docs in sync.

11. **ops: strengthen end-to-end ergonomics.** 🚧
    - Add smoke-test helpers that automatically export `AIJOURNAL_FAKE_OLLAMA=1` (or detect missing
      live models) before running summarize/facts/index search so CI/user runs avoid 404s.
    - Improve command scoring/reporting discipline in the runbook: treat missing model/network
      failures as 0/10 until automatically mitigated, and surface follow-up remediation steps.
    - Enhance health checks to distinguish fake payloads vs. live Ollama availability, annotating
      results to avoid overconfidence when working offline.
    - **Live verification 2025-10-27 (gpt-oss:20b @ 192.168.1.143).**
      - Re-ran summarize → ollama health on `/tmp/aijournal_live_run_202510262035`; score 179/190 (avg 9.42).
      - Structured calls (facts, suggestions, characterize, advise, chat) all validated against updated schemas; `review-updates` applied after adding a `planning` facet to `SelfProfile`.
      - Remaining issues: facts/suggestions still return empty payloads, chat feedback lacks claim-level references, and `chatd` shutdown continues to raise the SQLite cross-thread error.
      - Artefacts: refreshed summaries/microfacts, regenerated persona core + advice card, updated profile planning facet, packs within budget, chat transcripts recorded under `derived/chat_sessions/live-verify/`.
    - **Focused production to-dos (live mode only).**
      1. **Structured-output hardening.** ✅ _2025-10-29: `run_ollama_agent` now sanitizes fenced JSON and logs the cleaned payload on validation errors (`src/aijournal/services/ollama.py`, tests in `tests/test_ollama_services.py`). Chat/advise flows now require dedicated Pydantic response models with no heuristic fallbacks (see `src/aijournal/services/chat.py`, `src/aijournal/cli.py`). Facts/profile prompts updated to leverage summaries/sections so live runs emit meaningful payloads._
      2. **Prompt calibration for gpt-oss:20b.**  
         - Iterate on `extract_facts.md`, `profile_suggest.md`, and `characterize.md` so the model produces non-empty facts/suggestions on the seeded journal data.  
         - Add regression scripts (no fake mode) that run these prompts against `/tmp/aijournal_live_run_*` and save exemplar outputs.
      3. **chatd shutdown fix.** ✅ _2025-10-29: Retriever now uses a thread-safe SQLite connection guard (`check_same_thread=False` + lock) so chatd exits cleanly; regression covered by `tests/test_retriever.py::test_retriever_close_from_different_thread` and manual curl check._
      4. **Chat feedback improvements.** ✅ _2025-10-29: Chat prompt now enforces `[claim:<id>]` markers and CLI telemetry records detected claim markers, warning when none are present so feedback adjustments only trigger when claims are cited._

    - **Next Reliability Improvements (post-v0.3 polish).**
      1. **Auto-apply feedback batches asap.** ✅ _2025-10-29: `aijournal feedback-apply` scans `derived/pending/profile_updates/feedback_*.yaml`, replays each adjustment into `profile/claims.yaml`, and archives the batch. Coverage in `tests/test_cli_feedback.py` handles both multiple batches and empty queues._
      2. **Refine telemetry UX for missing claim markers.** ✅ _2025-10-29: CLI telemetry now includes the detected `claim_markers` array and prints a gentle hint when none are present so operators know feedback won’t adjust strengths._
      5. **Live runbook + model checks.**  
         - Detect missing models before command execution; prompt the operator to pull or switch to a known-good model (currently `gpt-oss:20b @ 192.168.1.143`).  
         - Document the full live rehearsal procedure, including how to rerun the command checklist and update the scoreboard.

Document each milestone in CHANGELOG.md once merged so README and PLAN stay aligned with shipped
surfaces.
