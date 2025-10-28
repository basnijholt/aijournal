# aijournal Architecture

This document is the authoritative reference for the current design of `aijournal`. It summarizes the system’s purpose, core concepts, data flows, and operational expectations so contributors and agents can reason about the project without consulting historical plans.

## 1. Vision and Principles

- Private, local-first assistant that runs entirely against a self-hosted Ollama instance—no cloud dependencies.
- Authoritative data is always Markdown or YAML so every insight remains transparent, diffable, and under version control.
- The primary outcome is a continuously maintained self-model that links motivations, values, habits, and goals back to concrete evidence.
- Favor small, composable Python modules with type hints and Pydantic validation. Every derived artifact must be reproducible from its sources.
- Optimize for an end-to-end pipeline from raw journal entry to actionable advice before investing in optional niceties.
- Reject legacy compatibility: regenerate artifacts instead of carrying migration shims. Failing fast on outdated formats keeps the system honest.

### 1.1 Guardrails

- Keep the ingestion → characterization → review pipeline simple and legible. Avoid deep inheritance hierarchies or hidden global state.
- Each CLI command records what it wrote; derived files must be safe to delete and rebuild.
- Prompts, config, and model selection stay deterministic—changing a prompt requires regenerating any dependent derived artifacts.

### 1.2 Operating Norms

- Treat Ollama calls, CLI invocations, and artifact inspection as routine; operators and agents may run the entire surface without additional approvals.
- Always run commands through `uv run …` so the project virtualenv and dependencies are active.
- Propagate new information into the authoritative profile and claims so persona packs, chat, and advice stay current.

### 1.3 Security and Privacy

- The system performs no network I/O beyond the configured Ollama endpoint. Keep the host local or on trusted hardware and audit configuration changes.
- Authoritative data (`data/`, `profile/`, `config/`, `prompts/`) is intentionally human-readable. Track it in Git, but avoid committing sensitive personal content unless the repository is private.
- Derived artifacts under `derived/` are reproducible and can be excluded from version control (`.gitignore`); delete and regenerate them when needed instead of editing by hand.
- Advisor mode enforces red lines from `profile/self_profile.yaml` (boundaries and ethics). Prompts that touch filtered topics must return general guidance with appropriate disclaimers.

## 2. System Architecture

`aijournal` is a Python package with a Typer-based CLI. Services orchestrate Ollama calls, retrieval, consolidation, and YAML I/O. Authoritative data (`data/`, `profile/`, `prompts/`, `config/`) is human-editable; anything under `derived/` is reproducible.

### 2.1 Repository Layout

```
aijournal/
  README.md
  ARCHITECTURE.md
  agents.md
  docs/
    workflow.md
    archive/PLAN-v0.3.md
  config/config.yaml
  src/aijournal/
    cli.py
    models/
    services/
    io/
    prompts/
  data/
    journal/YYYY/MM/DD/*.md
    normalized/YYYY-MM-DD/*.yaml
  profile/
    self_profile.yaml
    claims.yaml
  derived/
    summaries/
    microfacts/
    profile_suggestions/
    pending/profile_updates/
    persona/
    index/
    chat_sessions/
    advice/
  tests/
    fixtures/
```

### 2.2 Core Components

- **CLI (`src/aijournal/cli.py`)** – Entry points for initialization, ingestion, normalization, derivation, profile management, retrieval, chat, advisor mode, packs, and feedback.
- **Models (`src/aijournal/models/`)** – Pydantic schemas that validate every authoritative and derived artifact before it hits disk.
- **Services (`src/aijournal/services/`)** – Ollama client, retrieval/indexing, characterization, consolidation, chat orchestrator, advisor, and feedback handlers.
- **I/O utilities (`src/aijournal/io/`)** – Path mappers, YAML helpers, slug and ID generators, filesystem safety rails.
- **Prompts (`prompts/`)** – Markdown templates hashed into derived metadata to keep runs reproducible.

## 3. Core Concepts

### 3.1 Hierarchical Memory (L1→L4)

- **L1 – Persona Core:** Deterministic snapshot in `derived/persona/persona_core.yaml` plus top accepted claim atoms. Always included in packs and chat sessions.
- **L2 – Recent Activity:** The base day’s normalized entries with the last seven summaries and micro-facts, trimmed deterministically.
- **L3 – Extended Profile:** Complete accepted claims paired with the broader self-profile facets and recent advice or suggestions when relevant.
- **L4 – Background:** Prompts, config, and raw journals for the base day plus an optional history window—used when exporting context for external copilots.

### 3.2 Claim Atoms

`profile/claims.yaml` stores typed, scoped claim atoms with `{type, subject, predicate, value, scope}` alongside `strength`, `status`, and provenance. Scope captures domain, context, and conditions (for example weekday vs weekend) so downstream agents can reason precisely about when statements apply. Provenance lists source entry IDs and spans plus `first_seen`/`last_updated` timestamps.

### 3.3 Consolidation, Freshness, and Conflicts

- Consolidation weights existing strengths by `w_prev = min(1.0, log1p(n_prev))` and combines them with new evidence via `strength_new = clamp01((w_prev * strength_prev + w_obs * signal) / (w_prev + w_obs))`, where `w_obs = 1.0` and `signal` defaults to the evidence confidence.
- Effective strength decays at read time using `strength * exp(-lambda * staleness)` (`lambda ≈ 0.2`, `staleness = min(2, days_since / review_after_days)`).
- Conflicting evidence with distinct scopes is split into separate atoms; otherwise both claims are downgraded to `status: tentative`, strength reduced, and an interview question is queued.

### 3.4 Persona Core

`aijournal persona build` ranks claim atoms by `effective_strength × impact_weight` (weights defined in `config/config.yaml`) and selects enough claims to fit within the configured token budget alongside key facets (values, goals, boundaries, coaching preferences). The builder records trimming metadata, source mtimes, and refuses to run packs or chat when the persona core is stale.

### 3.5 Provenance, Re-Validation, and Impact Weights

- Each facet or claim records `method`, `user_verified`, `review_after_days`, and evidence references. `staleness = min(2.0, days_since_last_updated / review_after_days)` drives interview prioritization.
- Default impact weights: values/goals (1.5), decision_style (1.3), affect_energy (1.2), traits (1.0), social (0.9). Claim types (value, goal, boundary, trait, preference, habit, skill) inherit these weights for ranking.
- Freshness and impact control interview prompts and claim ordering in persona packs, advising the system where to probe next.

## 4. Data Flow Pipelines

Refer to `docs/workflow.md` for the operational command order. This section explains how each pipeline works under the hood.

### 4.1 Ingestion and Normalization

- `aijournal ingest <path>` hashes external Markdown files, stores raw snapshots in `data/raw/<hash>.md`, records manifest entries in `data/manifest/ingested.yaml`, and emits normalized YAML under `data/normalized/YYYY-MM-DD/`.
- `aijournal normalize <journal.md>` performs the same transformation for existing journal entries. Normalized files capture metadata (`id`, `created_at`, `title`, `tags`, `projects`, `mood`), structured sections, entities, summaries, and the original source path.
- The manifest prevents duplicate ingestion by SHA-256 hash and ties downstream artifacts back to their sources.

### 4.2 Daily Derivation Pipeline

Once normalized entries exist for a date, run the derivation commands in order:
1. `summarize` – writes `derived/summaries/<date>.yaml` with bullets, highlights, and TODO candidates.
2. `facts` – produces `derived/microfacts/<date>.yaml`, claim proposals, and consolidation previews.
3. `profile suggest` – generates `derived/profile_suggestions/<date>.yaml` with claim/facet upserts.
4. `profile apply` – merges accepted suggestions into `profile/claims.yaml` and `profile/self_profile.yaml`.

All outputs include `meta.{llm_model, prompt_path, prompt_hash, created_at}` and are validated against Pydantic models.

### 4.3 Characterization and Review Loop

- `aijournal characterize --date …` reads normalized entries, persona data, and manifest hashes to propose claim and facet updates plus interview prompts. Results land in `derived/pending/profile_updates/<timestamp>.yaml`.
- `aijournal review-updates --file … --apply` previews each proposal (including scope conflicts), updates the authoritative profile when approved, refreshes timestamps, and records which normalized entries and manifest hashes drove the change.
- Multiple batches per day are common; review and apply each batch before moving to retrieval.

### 4.4 Retrieval and Conversational Loop

- `aijournal index rebuild` transforms normalized entries into deterministic chunks (700–1200 characters, sentence-aware, including section headings) and stores:
  - SQLite FTS5 database (`derived/index/index.db`) with chunk metadata.
  - Annoy index (`derived/index/annoy.index`) keyed by SQLite row IDs.
  - Chunk manifests (`derived/index/chunks/YYYY-MM-DD.yaml`) and optional vector shards for inspection.
- Chat and advisor mode share the same orchestrator:
  1. Load the persona core and rank claims by effective strength (bounded by `chat.max_claims`).
  2. Retrieve journal chunks through the `Retriever` service (cosine similarity + recency score where `score = 0.7 * cosine + 0.3 * recency`, `recency = 1 / (1 + 0.05 * days_since)`), using `search_k = search_k_factor * k * trees`.
  3. Assemble context (persona core, selected claims, retrieved chunks with citations, conversation summary, coach preferences) under a shared token budget.
  4. Generate responses that include `[claim:<id>]` or `[entry:<normalized_id>#p<index>]` markers, respect `coaching_prefs.probing`, and optionally ask a clarifying question.
  5. Write transcripts, summaries, learnings, telemetry, and pending feedback batches to `derived/chat_sessions/<session>/`.
  6. Apply feedback nudges (+0.03 / −0.05 strength, clamped to [0, 1]) via `feedback-apply`.

### 4.5 Packs and Context Bundles

`aijournal pack --level Lx` assembles deterministic bundles using the shared token estimator (`token_estimator.char_per_token`, default 4.2). The command:
- Requires a fresh persona core (warns when stale).
- Logs planned token counts and trimmed artifacts (`meta.trimmed`) for reproducibility.
- Supports YAML or JSON output, optional history windows, and dry-run inspection.

### 4.6 Feedback and Strength Adjustments

- Chat feedback queues `derived/pending/profile_updates/feedback_*.yaml` capturing claim strength deltas and transcript context.
- `aijournal feedback-apply` replays each batch into `profile/claims.yaml`, archives applied files to `derived/pending/profile_updates/applied_feedback/`, and exits non-zero when nothing matched—useful for automation.

## 5. Data Models

Authoritative schemas (see `src/aijournal/models/authoritative.py`):
- `JournalEntry` – Markdown front matter plus body metadata.
- `NormalizedEntry` – Structured YAML mirror with sections, entities, summaries, and `source_path`.
- `SelfProfile` – Nested facets covering traits, values, goals, decision style, affect, boundaries, social context, and coaching preferences.
- `ClaimAtom` / `ClaimsFile` – Typed claims with scope, strength, provenance, `review_after_days`, and timestamps.

Derived schemas (see `src/aijournal/models/derived.py`):
- `DailySummary`, `MicroFactsFile`, `ProfileSuggestions`, `ProfileUpdateBatch`.
- `PersonaCoreFile`, `AdviceCard`, `InterviewSet`, `ChatTranscript`, `ChatTelemetry`, `IndexMeta`.
- Every derived YAML includes a deterministic `meta` block with the Ollama model, prompt path, prompt hash, creation time, and (where applicable) manifest hashes.

## 6. Prompts and Structured Output

- `prompts/summarize_day.md` – Bullets, highlights, TODOs. Output validated by `DailySummaryResponse`.
- `prompts/extract_facts.md` – Emits atomic statements with evidence and temporal bounds. Validated by `ExtractedFactsResponse`.
- `prompts/profile_suggest.md` – Proposes claim/facet upserts with rationale, method, and review cadence.
- `prompts/characterize.md` – Produces consolidated claim/facet updates tied to manifest hashes plus interview prompts.
- `prompts/interview.md` – Generates targeted follow-up questions using staleness and scope gaps.
- `prompts/advise.md` – Advisor mode, requiring `why_this_fits_you` with claim/facet citations, risks, mitigations, and tone alignment.

All structured prompts go through `run_ollama_agent`, which sanitizes JSON, retries validation up to `--retries`, and surfaces actionable errors when the model fails to comply. Changing a prompt invalidates the hashed metadata stored alongside derived outputs.

## 7. Retrieval Architecture

- **Chunking:** Deterministic boundaries (700–1200 characters) with sentence awareness and section headings for context.
- **Storage:** SQLite FTS5 database for metadata & text (`fts5` is a required compile option) and an Annoy index for vectors.
- **Vectors:** Embeddings generated via `nomic-embed-text` served by Ollama. `derived/index/meta.json` records embedding dimension, build time, ann_trees, search_k_factor, and whether fake mode ran.
- **Search:** `Retriever.search` loads the Annoy neighbors with `search_k = search_k_factor * k * ann_trees`, filters by tags/date/source, then reranks using cosine similarity and recency.
- **Inspection:** Chunk manifests mirror the indexed content for human audits or external tooling.
- **Failure Modes:** Missing indexes result in explicit errors directing operators to run `aijournal index rebuild`.

## 8. Configuration and Environment

- `config/config.yaml` captures model defaults, embedding model, temperature, token estimator, impact weights, advisor and chat settings, index parameters, and persona budgets.
- Environment overrides:
  - `AIJOURNAL_CONFIG` – alternate config path.
  - `AIJOURNAL_MODEL`, `AIJOURNAL_OLLAMA_HOST` – run-time model/endpoint selection.
  - `AIJOURNAL_FAKE_OLLAMA=1` – deterministic fixtures for tests and CI.
- Live-mode defaults (see `agents.md`): remote Ollama at `http://192.168.1.143:11434`, chat/advice model `gpt-oss:20b`, embedding model `nomic-embed-text`, commands executed via `uv run -- bash -lc '…'`.
- Always ensure runs start from a clean git tree; archive live artifacts under `/tmp/aijournal_live_run_*` rather than touching the repo directly.

## 9. Performance Considerations

- Journals are small and YAML parsing is fast. Retrieval performance hinges on the Annoy index; rebuilding remains quick even with tens of thousands of chunks.
- Structured-output commands (summaries, facts, characterize, advise) run sequentially and typically complete within seconds under `gpt-oss:20b`. Increase `--timeout` when working with slower models.
- Caching hooks (`derived/cache/`) can capture prompt outputs keyed by `(model, prompt_hash, inputs_hash)` if future workloads demand reuse.
- Keep an eye on retrieval latency via chat telemetry; maintaining `index.meta.json` helps correlate ANN parameters (`ann_trees`, `search_k_factor`) with observed timings.

## 10. Development Workflow

Contributor setup, testing expectations, and linting tools are covered in [CONTRIBUTING.md](./CONTRIBUTING.md). In short: manage dependencies with `uv`, run `uv run pytest` regularly, and use Ruff + mypy before submitting changes.

## 11. Operational Runbooks

- **Daily workflow:** Follow `docs/workflow.md` for the canonical command order—from `init` and manual journaling through normalize, summarize, facts, profile suggest/apply, characterize, review, rebuild index, persona build, pack, chat/advice, and feedback application.
- **Live-mode rehearsal:** `agents.md` captures the full 350/350 run checklist, environment variables, model verification, prompt calibration lessons, chat feedback expectations, server shutdown validation, and post-run cleanup. Use it whenever running against the remote `gpt-oss:20b` instance with real data.
- Maintain a run log (e.g., `run_log.md`) when executing long rehearsals, recording score, command, artifacts, and troubleshooting notes.

## 12. Quality Targets and Acceptance Criteria

- Persona core (L1) includes top values, current goals, boundaries, coaching preferences, and ≥10 high-strength claim atoms within ~1200 tokens.
- Claim atoms are typed and scoped; consolidation merges evidence deterministically, handles conflicts via scope splitting or tentative downgrades, and queues interview prompts for ambiguity.
- Retrieval-backed chat cites claims or journal evidence ≥90% of the time on seed QA and keeps latency <150 ms on 50k+ chunks.
- Feedback loop: thumbs up/down adjusts claim strengths immediately and queues learnings into pending updates.
- Interview ranking blends staleness, uncertainty, and missing scopes; advisor mode leverages the same signal for assumptions and recommendations.
- Packs (L1–L4) respect token budgets, include trimming metadata, and reuse the shared persona core.

## 13. Glossary

- **Authoritative data** – Human-edited sources under `data/`, `profile/`, `prompts/`, and `config/`.
- **Derived artifacts** – Reproducible outputs under `derived/`; safe to delete and regenerate.
- **Claim Atom** – Typed, scoped statement with strength and provenance used by persona, retrieval, chat, and advice.
- **Persona Core** – Deterministic L1 bundle that seeds packs, chat, and advisor context.
- **Impact Weight** – Per-facet or claim-type weighting controlling prioritization in persona, interviews, and advice.
- **Pending Batch** – File under `derived/pending/profile_updates/` holding proposed profile or claim changes awaiting review.
- **Claim Marker** – `[claim:<id>]` token inserted into chat responses so feedback adjustments map to specific claims.
- **Feedback Batch** – Strength adjustments generated from chat feedback, applied via `aijournal feedback-apply`.
- **Manifest Hash** – SHA-256 digest stored in `data/manifest/ingested.yaml` linking normalized entries and derived artifacts to their source materials.
