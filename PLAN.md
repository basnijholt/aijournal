# PLAN.md — aijournal (Local‑First, YAML‑Centric) v0.1

A complete, self‑contained blueprint to implement a private, offline, reproducible personal self‑modeling journal using local Ollama. Primary data stays in human‑readable files (YAML/Markdown). All indexes/summaries are reproducible artifacts. Includes an Advisor Mode to give personalized, constraint‑aware advice using your stored profile and claims.

---

## 1. Vision and Principles

- Private and offline: runs entirely on localhost with Ollama.
- Authoritative data in YAML/Markdown; derived artifacts are reproducible.
- KISS: type‑hinted Python with dataclasses; small, composable commands.
- Evidence‑linked profile with confidence, provenance, and freshness.
- Hierarchical memory that fits in context (L1→L4).
- Interviewer asks targeted, low‑friction follow‑ups to close gaps.
- Advisor Mode produces actionable recommendations aligned with your values, goals, boundaries, and coaching preferences.
- Frequent commits; tests first where sensible; fake LLM mode for CI.

Non‑goals v0.1:
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
    models/                     # dataclasses + (de)serialization helpers
    services/                   # ollama client, derivation, ranking, advisor
    io/                         # YAML/MD I/O, path mappers
    prompts/                    # prompt loaders, hashing
  tests/                        # unit + functional + fixtures
    fixtures/
  config/
    config.yaml                 # model, paths, temps
    schemas/                    # JSON Schemas for validation
      journal_entry.json
      normalized_entry.json
      summary.json
      microfacts.json
      claims.json
      self_profile.json
      profile_suggestions.json
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
    index/                      # optional embeddings later
```

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
    - `uv add typer pyyaml httpx cattrs python-dateutil`
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
  - One‑off tools: `uvx ruff` / `uvx mypy` if you prefer ephemeral tools (optional; dev deps already pinned).

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

### 3.4 Claims (evidence‑linked)
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

- L1 (Active): today’s normalized entries + last summary (≤ 400 tokens).
- L2 (Recent): last 7 days summaries + high‑confidence micro‑facts (≤ 900 tokens).
- L3 (Profile Core): accepted claims + key facets from `self_profile.yaml` (≤ 1800 tokens).
- L4 (Background): weekly/quarterly aggregates or embeddings (optional).

Command: `aijournal pack --level L3 --out /tmp/context.txt`

---

## 8. Ollama Integration

- Default endpoint: `http://localhost:11434`.
- Models: `llama3.1:8b-instruct` (configurable).
- Client: thin wrapper with `generate(prompt:str, json_schema?:dict) -> str|dict`.
- Deterministic tests: `AIJOURNAL_FAKE_OLLAMA=1` to use local fixtures.
- Metadata stamped into all derived files: `llm_model`, `prompt_path`, `prompt_hash`, `created_at`.

Health check:
- `aijournal ollama health` returns model list and selected default.

---

## 9. Configuration

- Path: `config/config.yaml`
  - `model: "llama3.1:8b-instruct"`
  - `temperature: 0.2`
  - `seed: 42`
  - `paths: {data, profile, derived, prompts}`
  - `impact_weights: {...}`
  - `advisor: {max_recos: 3, include_risks: true}`
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
- `aijournal profile status` — list stale/high‑impact facets/claims with ranks.
- `aijournal profile suggest [--since YYYY-MM-DD]` — write suggestions YAML (facets+claims).
- `aijournal profile apply [--file derived/profile_suggestions/...]` — interactive accept/merge.
- `aijournal interview --max 4` — prioritized probes; uses 8 high‑impact questions when gaps exist.
- `aijournal advise "question" [--level L1|L2|L3] [--max 3]` — Advisor Mode; generates `derived/advice/...yaml` and prints a concise summary to stdout.
- `aijournal pack --level L1|L2|L3|L4 --out path` — assemble context pack for prompts.
- `aijournal ollama health` — verify local model availability.

Interactive apply (text‑mode):
- Show each suggestion diff (YAML delta), accept/skip, then write back to authoritative file(s) and update timestamps/freshness.

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
- Dataclass (de)serialization for JournalEntry, NormalizedEntry, Fact, Claim, SelfProfile facets.
- Path mappers and slug/ID generators are deterministic.
- Staleness ranking and impact weights.

Functional (CLI):
- `init/new/normalize` produces expected files and paths.
- `summarize/facts` under fake mode write valid derived YAML; validate against schemas; snapshot key sections.
- `advise` under fake mode returns a valid Advice Card and respects tone/boundaries.

Schema:
- JSON Schemas for each artifact under `config/schemas/` including `advice.json`.
- `pytest` validates real files against schemas.

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

- `aijournal new --fake N` generates N entries with plausible frontmatter and content (no LLM).
- A deterministic seed yields stable outputs for tests.

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
- Dependencies (runtime): `typer`, `PyYAML`, `httpx`, `cattrs`, `python-dateutil`
- Dev: `pytest`, `pytest-cov`, `mypy`, `ruff` (optional), `hypothesis` (optional)

Conventions:
- Dataclasses for models; `cattrs` for structure/unstructure.
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
- Add `.gitignore`, `README.md`, `PLAN.md`, `justfile`, `config/config.yaml`, empty `config/schemas/`.
- Add empty `profile/claims.yaml` and seed `profile/self_profile.yaml` with the provided YAML.
- Commit `pyproject.toml` and `uv.lock`.
- Tests folder scaffold.

2) feat(cli): init
- Implement idempotent directory creation with clear output.
- Tests: initializing twice is no‑op and returns success.

3) feat(core): models + schemas
- Dataclasses for journal, normalized, summary, micro‑facts, claim, self profile facets, suggestions, interviews, advice.
- JSON Schemas for each artifact; validation helpers.
- Tests: (de)serialization + schema validation round‑trips.

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
- Tests: schema validation + snapshot of bullets.

8) feat(derive): facts
- Prompt call → `derived/microfacts/DATE.yaml` with evidence locators.
- Tests: schema validation + evidence linking to normalized entry id.

9) feat(profile): ranking + status
- Implement staleness and impact ranks.
- `aijournal profile status` summarizes top stale/high‑impact facets and claims.
- Tests: deterministic ranking.

10) feat(profile): suggest
- Aggregate micro‑facts and diff `self_profile.yaml` + `claims.yaml` → suggestions YAML.
- Tests: default `user_verified=false`, `method` present, schema valid.

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
- `aijournal pack --level L1|L2|L3|L4` assembles context; token‑aware trimming.

---

## 22. Acceptance Criteria (MVP)

- Can add entries, normalize, derive summaries and micro‑facts via local Ollama.
- Can propose and apply profile/claim updates with provenance and re‑validation cadence.
- Interviewer outputs targeted questions prioritizing stale/high‑impact facets.
- Advisor Mode produces personalized, constraint‑aware Advice Cards and concise terminal summaries.
- All artifacts are human‑readable with JSON Schema validation.
- Fake LLM mode enables offline, deterministic tests.
- Context pack L3 comfortably ≤ 1800 tokens.

---

## 23. Glossary

- Authoritative: files edited by the user that define truth (`data/`, `profile/`, `prompts/`, `config/`).
- Derived: reproducible files created from authoritative inputs (`derived/`).
- Facet: a structured field in `self_profile.yaml` (e.g., `traits.big_five.openness`).
- Claim: evidence‑linked statement with confidence and freshness in `claims.yaml`.
- Advice Card: a reproducible artifact with tailored recommendations and traceable rationale.

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
