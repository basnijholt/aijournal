# aijournal

Local-first, YAML-centric personal self-modeling agent. All authoritative data lives in human-readable files; derived artifacts are reproducible via local Ollama. See `PLAN.md` for the full persona-first roadmap (typed claim atoms, retrieval-backed chat, persona core packs).

## Getting Started

```sh
uv sync
uv run pytest -q
```

- Runtime deps beyond Typer/PyYAML/httpx/pydantic/dateutil: `numpy`, `annoy`, `fastapi`, `uvicorn`, `orjson`. Install once via `uv add ...`; everything stays local-first.
- Default embedding model: `nomic-embed-text` via Ollama; configurable through `config/config.yaml` or `AIJOURNAL_MODEL`.

- `config/config.yaml` stores runtime defaults (model, temperature, advisor settings).
- `src/aijournal/models/` defines the Pydantic schemas the CLI enforces on every write.
- `prompts/*.md` contains the Ollama prompt templates for summarize/facts/profile/advise.
- `profile/` seeds an initial self-profile plus an empty claims list so commands have context.

Run `aijournal init` inside a fresh directory to materialize `data/`, `derived/`, `prompts/`, etc.; repeat executions are idempotent.

### LLM runtime modes

- **Live mode (default):** calls your local Ollama server using the Python `ollama` client. Set
  `AIJOURNAL_OLLAMA_HOST=http://localhost:11434` if you run Ollama elsewhere. Override the model with
  `AIJOURNAL_MODEL="llama3.1:8b-instruct"` when needed.
- **Fake mode (tests/CI):** `export AIJOURNAL_FAKE_OLLAMA=1` to bypass Ollama and return deterministic
  fixtures. The code automatically falls back to the fake path if a live request fails, so scripts remain
  robust even if the model is offline.

### Claim atoms & persona core

- Claims now live as typed, scoped atoms inside `profile/claims.yaml` with `{type, subject, predicate, value, scope, strength, provenance}` fields.
- `aijournal persona build` regenerates `derived/persona/persona_core.yaml` (≤ ~1200 tokens) by selecting top claim atoms + key profile facets. Packs/chat always include this file as L1 context.
- There is intentionally **no** legacy format support—if schema changes, re-run `aijournal init` and regenerate data rather than carrying migration code.

## Usage

Fake LLM-powered commands run in fixture mode when `AIJOURNAL_FAKE_OLLAMA=1`:

```sh
export AIJOURNAL_FAKE_OLLAMA=1
```

Most commands below are safe to re-run: they detect unchanged inputs and either skip writes or report what was "already" present. The lone exception is `aijournal new`, which refuses to overwrite an existing slug and exits non-zero if you try to create the same entry twice.

### Initialize the workspace

```sh
aijournal init --path ~/journal
```

Creates the full layout (config/profile/data/derived/prompts). Subsequent runs just print counts of existing directories/files, keeping automation idempotent.

### Capture a new journal entry

```sh
cd ~/journal
aijournal new "Morning sync" --tags focus planning
```

Emits `data/journal/YYYY/MM/DD/<slug>.md` with YAML frontmatter and refuses to overwrite an existing slug.

### Generate fake entries (fixtures / demos)

```sh
aijournal new --fake 3 --seed 7 --tags focus planning
```

Produces three Markdown files with full frontmatter (`id`, `created_at`, `title`, `tags`, `projects`, `mood`) plus short body paragraphs. The command never calls Ollama and is safe to run offline; existing slugs are skipped. Provide `--seed` for deterministic fixtures (great for tests/CI) and optionally layer `--tags` to override the auto-generated tag sets.

### Build or tail the retrieval index

```sh
aijournal index rebuild          # one-shot rebuild (SQLite + Annoy) from normalized YAML
aijournal index tail --since 7d  # optional helper: follow manifest entries and index new files
```

- Index lives under `derived/index/` (`index.db`, `annoy.index`, `meta.json`).
- Chunking is deterministic (700–1200 characters, sentence boundaries) and every chunk stores normalized_id/date/tags.
- When Annoy is unavailable the CLI falls back to pure FTS search and annotates `meta.mode: fake(fallback)`.

### Ingest existing Markdown (blogs, notes)

Use the ingestion agent to normalize entire directories of Markdown or Hugo posts. By default it
talks to your local Ollama server (set `AIJOURNAL_FAKE_OLLAMA=1` to use the deterministic fake
parser in tests/CI):

```sh
aijournal ingest /home/basnijholt/Work/nijho.lt/content/post --source-type blog
```

Each ingested file is hashed (manifest stored at `data/manifest/ingested.yaml`), a raw snapshot is
saved under `data/raw/<hash>.md`, and normalized YAML lands in `data/normalized/<DATE>/...`. If your
Ollama daemon is listening on a non-default address, set `AIJOURNAL_OLLAMA_HOST` accordingly.

### Normalize Markdown into YAML

```sh
aijournal normalize data/journal/2025/02/03/morning-sync.md
```

Produces `data/normalized/2025-02-03/<entry_id>.yaml`. Files are only rewritten when content changes.

### Summaries

```sh
aijournal summarize --date 2025-02-03
```

Calls `prompts/summarize_day.md` through Ollama and writes `derived/summaries/<DATE>.yaml` with
`bullets`, `highlights`, `todo_candidates`, plus a stamped `meta` block. Set
`AIJOURNAL_FAKE_OLLAMA=1` for deterministic fixtures.

### Micro-facts

```sh
aijournal facts --date 2025-02-03
```

Uses `prompts/extract_facts.md` to create `derived/microfacts/<DATE>.yaml` filled with
evidence-backed statements. Fake mode falls back to the deterministic placeholder generator for CI.

### Ollama health check (fake mode)

```sh
export AIJOURNAL_FAKE_OLLAMA=1
aijournal ollama health
```

Prints the fixture's advertised `models` array and its `default_model`, for example:

```
models:
  - llama3.1:70b-instruct
  - llama3.1:8b-instruct
default_model: llama3.1:8b-instruct
status: ok (fake)
```

The fake health probe never touches the network, so it is safe to call repeatedly in automation to confirm Ollama wiring without mutating any files.

### Profile status quick-look

```sh
aijournal profile status
# alias: aijournal profile-status
```

Ranks facets/claims needing review using `config/config.yaml` impact weights.

### Advisor mode

```sh
aijournal advise "Should I block mornings for focus?"
```

Builds an advice card under `derived/advice/<DATE>/<slug>.yaml` using `prompts/advise.md`, citing the
facets/claims referenced in each recommendation. Fake mode remains available for CI by setting
`AIJOURNAL_FAKE_OLLAMA=1`.

### Retrieval-backed chat (CLI or FastAPI)

```sh
# CLI/TUI session
aijournal chat

# FastAPI server (default http://localhost:8765)
aijournal chatd --port 8765
```

- Each turn is intent-classified (`advice|planning|reflection|qa_about_me|meta`), retrieves top claim atoms + journal chunks through the Annoy/SQLite index, and assembles context with persona core + conversation summary under the configured token budget.
- Responses must cite claims (`[claim:pref.deep_work.window]`) and/or journal entries (`[entry:2025-10-25_x9t3#p0]`) and may ask at most one clarifying question if `coaching_prefs.probing` allows.
- Learnings from **user** messages are extracted into micro-facts, run through the consolidation service, and queued in `derived/pending/profile_updates/…`; session artifacts live under `derived/chat_sessions/<session_id>/{transcript.jsonl, summary.yaml, learnings.yaml}`.
- Tune behavior via `config.chat` (`max_retrieved_chunks`, `max_claims`, `follow_up_enabled`, `write_back_facts`). Fake mode stamps `meta.mode: fake(fallback)` whenever LLM calls fail and heuristics are used.

### Profile suggestions

```sh
aijournal profile suggest --date 2025-02-03
```

Runs `prompts/profile_suggest.md` with the current profile + claims and stores
`derived/profile_suggestions/<DATE>.yaml`. Outputs are validated against the
`ProfileSuggestions` Pydantic model before being written. Enable fake mode for deterministic fixtures.

### Apply profile suggestions

```sh
aijournal profile apply --date 2025-02-03 --yes
```

Applies the derived suggestions into `profile/self_profile.yaml` and `profile/claims.yaml`, updating `last_updated` stamps only when something changes.

### Regenerate persona core (L1)

```sh
aijournal persona build
```

Writes `derived/persona/persona_core.yaml` by ranking claim atoms with a
`strength × impact × decay` score, then trimming under the configured token
budget. The persona block keeps the most important facets from
`profile/self_profile.yaml` (values/goals/boundaries/coaching prefs, etc.) plus
the highest ranking claims, and records trimming metadata in
`meta.trimmed`. Override the defaults with `--token-budget`, `--max-claims`, or
`--min-claims` (all mirrored under `config/config.yaml` → `persona.*`). Token
estimates respect `token_estimator.char_per_token` (default 4.2). The generated
file is always included in packs/chat as the canonical L1 persona core and can
be regenerated safely anytime.

### Characterize normalized entries

```sh
aijournal characterize --date 2025-02-03
```

Runs the characterization agent (or deterministic fake mode) and emits a batch
under `derived/pending/profile_updates/<DATE>-<TIMESTAMP>.yaml`. Each batch
captures claim/facet proposals plus the manifest hashes that justify them.

### Review pending updates

```sh
aijournal review-updates --apply
```

Lists the latest batch (or the one specified via `--file`) and merges accepted
changes into `profile/` when `--apply` is provided. Use it as a manual approval
step before updating the authoritative self-model.

### Pack context bundles (L1–L4)

```sh
# Inspect planned files without writing anything
aijournal pack --level L2 --dry-run

# Persist a reusable bundle (rewrites only when content changes)
aijournal pack --level L1 --output derived/packs/l1.yaml

# Include advice + profile suggestions (optional) in an L3 pack
aijournal pack --level L3 --date 2025-02-03 --max-tokens 2800

# L4 with 2 days of history, prompts, config, and raw journals
aijournal pack --level L4 --date 2025-02-03 --history-days 2 --dry-run

# Emit an L4 pack as JSON for piping into another tool
aijournal pack --level L4 --date 2025-02-03 --history-days 1 --format json > /tmp/context-l4.json
```

`pack` now follows the standardized layers:

- **L1 (Persona Core):** `derived/persona/persona_core.yaml` + top accepted claim atoms.
- **L2 (Recent Activity):** today’s normalized entries + last 7 summaries/micro-facts.
- **L3 (Extended Profile):** complete claims + extended self_profile facets + optional advice/suggestions for the day.
- **L4 (Background):** prompts, config, raw journals for base day ± `--history-days`.

All packs log `meta.token_estimator` (default `char/4.2`), `planned_tokens`, and any trimmed files (`role`, `path`, `reason`).

### Retrieval index & filters

```sh
aijournal index rebuild
aijournal index tail
```

- `derived/index/index.db` stores chunk metadata + FTS5 virtual table; `derived/index/annoy.index` stores embeddings; `meta.json` records embedding model/dim/build timestamp and whether fake mode ran.
- Chunking is deterministic (700–1200 chars, sentence boundaries) and each chunk stores `{normalized_id, date, tags, source_type, chunk_index, tokens}`.
- Prefer the ANN-backed path for speed, but you can opt out of databases: store chunk manifests under `derived/index/chunks/YYYY-MM-DD.yaml` (plus optional `.npy` vector shards) and run pure cosine/text search without SQLite—everything remains human-readable and reproducible.
- `Retriever.search("question about deep work", k=12, filters=...)` (see `src/aijournal/services/retriever.py`) powers chat/advice, combining Annoy cosine scores with a light recency boost; when Annoy/SQLite are unavailable the CLI streams the YAML chunk manifests instead and stamps `meta.mode: fake(fallback)`.

### Configuration quick reference

`config/config.yaml` now includes:

- `embedding_model`, `token_estimator.char_per_token`, and `index.{rebuild_threshold,ann_trees,search_k_factor}`.
- `chat.{max_retrieved_chunks,max_claims,follow_up_enabled,write_back_facts}` to tune the orchestrator.
- Expanded `impact_weights` covering claim atom types (`value`, `goal`, `boundary`, `trait`, `preference`, `habit`, `skill`).
- `persona.{token_budget,max_claims,min_claims}` for persona core sizing + minimum claim coverage.

Trimming now prioritizes raw journal content first; when a pack exceeds `--max-tokens`, entries are zeroed in deterministic role order and `meta.trimmed` captures a list of `{role, path}` objects so you can inspect exactly what was removed. Dry-run output still lists every planned file with its token estimate, and both YAML/JSON payloads remain deterministic for caching or scripting.

## Prompts & Validation

- Prompt templates live under `prompts/` and are hashed into every derived artifact's `meta.prompt_hash`.
- Pydantic models under `src/aijournal/models/` are enforced on every write; violating payloads abort the command with actionable errors.
- The combination of typed Pydantic models + deterministic YAML makes derived artifacts reproducible—delete and regenerate any `derived/` subtree with confidence.

## Pre-commit Hooks

Install [pre-commit](https://pre-commit.com/) once, then enable the hooks locally:

```sh
uvx pre-commit install
```

To dry-run the hooks across the repo without touching staged files:

```sh
uvx pre-commit run --all-files --show-diff-on-failure
```

To run the full hook suite before pushing:

```sh
uvx pre-commit run --all-files
```
