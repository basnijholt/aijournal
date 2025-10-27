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

- **Live mode (default):** uses Pydantic AI's Ollama provider via the shared `run_ollama_agent`
  helper. `build_ollama_config_from_mapping` fuses `config/config.yaml`, environment overrides, and
  per-command tweaks so every CLI surface hits the same configuration pipeline. Set
  `AIJOURNAL_OLLAMA_HOST=http://localhost:11434` if you run Ollama elsewhere, or
  `AIJOURNAL_MODEL="llama3.1:8b-instruct"` to pick a different model.
- **Fake mode (tests/CI):** `export AIJOURNAL_FAKE_OLLAMA=1` to route every agent call through
  deterministic fixtures. Commands automatically fall back to fake mode if a live request fails, so
  scripts remain robust even when Ollama is offline.

### Claim atoms & persona core

- Claims now live as typed, scoped atoms inside `profile/claims.yaml` with `{type, subject, predicate, value, scope, strength, provenance}` fields.
- `aijournal persona build` regenerates `derived/persona/persona_core.yaml` (≤ ~1200 tokens) by selecting top claim atoms + key profile facets. Packs/chat always include this file as L1 context.
- There is intentionally **no** legacy format support—if schema changes, re-run `aijournal init` and regenerate data rather than carrying migration code.
- Use `aijournal persona status` anytime you edit `profile/*.yaml` to confirm the cached persona core matches the latest mtimes. The builder now records source file mtimes and `pack` warns when the persona core is stale or missing so you remember to rebuild before sharing context bundles.

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
aijournal index search "deep work ideas" --tags focus --date-from 2025-02-01
```

- Index lives under `derived/index/` (`index.db`, `annoy.index`, `meta.json`).
- Chunking is deterministic (700–1200 characters, sentence boundaries) and every chunk stores normalized_id/date/tags.
- Retrieval relies on Annoy + SQLite FTS5; if those artifacts are missing, commands error loudly so you can rebuild with `aijournal index rebuild`.
- `aijournal index search` reuses the Retriever service to stream scored snippets with source path/date metadata, honoring `--tags`, `--source`, `--date-from`, and `--date-to` filters.
- FTS5 is a hard requirement: verify with `python - <<'PY'\nimport sqlite3\nprint('fts5' in sqlite3.connect(':memory:').execute(\"pragma compile_options\").fetchall().__str__().lower())\nPY`. If it prints `False`, install an FTS5-enabled SQLite and rebuild Python (macOS: `brew install sqlite` then reinstall Python via `pyenv` or `uv`; Linux: `sudo apt install libsqlite3-dev` before building Python).
- After editing retrieval-related code run `uv run pytest -q` to ensure the CLI and retriever fixtures remain deterministic.

### Ingest existing Markdown (blogs, notes)

Use the ingestion agent to normalize entire directories of Markdown or Hugo posts. By default it
talks to your local Ollama server (set `AIJOURNAL_FAKE_OLLAMA=1` to use the deterministic fake
parser in tests/CI):

```sh
aijournal ingest /home/basnijholt/Work/nijho.lt/content/post --source-type blog
```

Each ingested file is hashed (manifest stored at `data/manifest/ingested.yaml`), a raw snapshot is
saved under `data/raw/<hash>.md`, and normalized YAML lands in `data/normalized/<DATE>/...`. If your
Ollama daemon is listening on a non-default address, set `AIJOURNAL_OLLAMA_HOST` accordingly. Large
directories can take a couple of minutes to process—let the command run to completion or increase
your wrapper's timeout if you're invoking it from automation.

Downstream LLM-backed commands (`summarize`, `facts`, `profile suggest`, `characterize`) now share a
consistent ergonomics layer: `--progress` surfaces per-entry logging, `--timeout` tunes the per-call
budget, and `--retries` controls structured-output retries before surfacing an explicit failure. All
of them resolve model/temperature/host via `build_ollama_config_from_mapping` before delegating to
`run_ollama_agent`, so behavior stays aligned across CLI surfaces. Fake mode remains available for
CI/tests by setting `AIJOURNAL_FAKE_OLLAMA=1`.

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

`summarize` (and the other LLM-backed commands) now streams responses through
Pydantic AI's structured output validation. The CLI requests a `DailySummaryResponse`
Pydantic model from the model and retries schema failures up to `--retries`
times (default 1). Use `--timeout` to extend the per-call budget (defaults to
120s) and `--progress` to print each normalized entry before the request is
sent. If the model keeps returning invalid JSON after the configured retries, the
command aborts with an actionable error so you can inspect the upstream output.

### Micro-facts

```sh
aijournal facts --date 2025-02-03
```

Uses `prompts/extract_facts.md` to create `derived/microfacts/<DATE>.yaml` filled with
evidence-backed statements. Outputs are validated against the `MicroFactsFile`
model, and fake mode now emits typed `MicroFact` objects for each entry so the
structure matches real runs even in CI. Each run now also attaches the derived
claim proposals and a consolidation preview: micro-facts are converted into
`ClaimProposal` atoms, pushed through the shared `ClaimConsolidator`, and the
resulting `preview.claim_events` mirror the output of `review-updates --dry-run`.
Any conflicts are scope-split (weekday vs. weekend, solo vs. team) before falling
back to tentative downgrades, and queued follow-up prompts surface in the CLI so
you can jump straight into `aijournal interview`.

Pass `--progress` to watch the entry-by-entry feed, `--timeout` to adjust the
per-call budget, and `--retries` to control how many schema failures trigger a
retry. Responses are validated against the `ExtractedFactsResponse` schema; if
validation still fails after the configured retries, the command stops with an
error instead of silently emitting heuristics.

### Ollama health check (fake mode)

```sh
export AIJOURNAL_FAKE_OLLAMA=1
aijournal ollama health
```

Prints the fixture's advertised `models` array and its `default` model, for example:

```
endpoint: fake://ollama
default: llama3.1:8b-instruct
models:
  - name: llama3.1:8b-instruct
    size: 8B
    quant: Q4_K_M
  - name: llama3.1:70b-instruct
    size: 70B
    quant: Q4_K_M
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

- Advisor Mode now consumes the same interview ranking signal used by `aijournal interview`, so
  follow-up prompts and scope gaps surface in the assumptions/steps without extra prompting.

### Retrieval-backed chat (CLI)

```sh
aijournal chat "What did I focus on last week?"
```

Streams a short answer grounded in your persona core plus retrieved journal chunks. Each response
includes inline `[entry:<normalized_id>#p<idx>]` citations, a telemetry summary, and—in live mode—an
optional follow-up question that respects `coaching_prefs.probing`. The command exits early when
prerequisites are missing—ensure `derived/persona/persona_core.yaml`, `derived/index/index.db`, and
`derived/index/annoy.index` exist (rebuild them with `aijournal persona build` and
`aijournal index rebuild`). Setting `AIJOURNAL_FAKE_OLLAMA=1` keeps the loop deterministic for
tests/CI.

- Use `--session <id>` (or accept the autogenerated `chat-YYYYMMDD-HHMMSS`) to append to a running
  conversation. The turn is archived under `derived/chat_sessions/<session>/` as
  `{transcript.jsonl,summary.yaml,learnings.yaml}` when `--save` is left enabled (default).
- Provide `--feedback up|down` to nudge cited claim strengths (+0.03 / −0.05, clamped 0–1). Each
  feedback event queues a pending update in `derived/pending/profile_updates/feedback_*.yaml`
  summarising the adjustments.
- Toggle `--no-save` for ephemeral answers that should not hit the transcript store.
- Structured telemetry for every turn is emitted to stderr as a compact JSON line (`event:
  chat.telemetry`) so you can tail logs during automation.

### Chat daemon (FastAPI)

```sh
aijournal chatd --host 127.0.0.1 --port 8080
```

Starts a FastAPI service that reuses the CLI orchestrator. `POST /chat` accepts the same payload
shape (`question`, `top`, optional filters, `session_id`, `feedback`, `save`) and streams
NDJSON frames: a metadata header (telemetry, session, feedback adjustments) followed by the answer
payload. Responses persist to `derived/chat_sessions/` when `save` is true, and claim-feedback
nudges are applied automatically just like the CLI.

### Profile suggestions

```sh
aijournal profile suggest --date 2025-02-03
```

Runs `prompts/profile_suggest.md` with the current profile + claims and stores
`derived/profile_suggestions/<DATE>.yaml`. Outputs are validated against the
`ProfileSuggestions` Pydantic model before being written. Fake mode returns the
same typed structures (claim upserts + facet updates) to keep pipelines consistent.

The live command asks the model for a simplified `suggestions` array (claims and
facets) via Pydantic AI's structured output support. Use `--progress`, `--timeout`, and
`--retries` to mirror the ergonomics of the other pipelines; if schema validation
fails after the configured retries, the CLI exits with an error so upstream
prompt/debugging is explicit.

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

Check whether the cached persona core matches the latest profile edits at any
time:

```sh
aijournal persona status
```

The status command compares the recorded mtimes for `profile/*.yaml` against the
current filesystem and prints a yellow reminder (without blocking) when you need
to re-run `persona build`.

### Characterize normalized entries

```sh
aijournal characterize --date 2025-02-03
```

Runs the characterization agent (or deterministic fake mode) and emits a batch
under `derived/pending/profile_updates/<DATE>-<TIMESTAMP>.yaml`. Each batch
captures claim/facet proposals plus the manifest hashes that justify them.
`--progress`, `--timeout`, and `--retries` mirror the other commands. The
structured response must satisfy the `CharacterizeResponse` schema; otherwise
the CLI prints a warning, retries if configured, and finally falls back to the
deterministic profile-updater when schema validation keeps failing. Interview
prompts returned by the model are merged with the consolidation preview so they
surface in the pending batch.

### Review pending updates

```sh
aijournal review-updates --apply
```

Lists the latest batch (or the one specified via `--file`) and merges accepted
changes into `profile/` when `--apply` is provided. Use it as a manual approval
step before updating the authoritative self-model.

### Interview probes

```sh
aijournal interview --date 2025-02-03
```

Ranks facets/claims using impact weights and either (a) generates targeted questions via
`prompts/interview.md` (live mode) or (b) falls back to deterministic heuristics in fake mode.
The command honours `coaching_prefs.probing.max_questions`: set it to `0` to suppress follow-ups,
or tweak the value to control how many concise questions are returned. Structured responses are
validated against the `InterviewSet` schema, and any LLM failure gracefully reverts to the
heuristic probes used previously.

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
- **L2 (Recent Activity):** today's normalized entries plus the most recent 7 summaries/micro-facts.
- **L3 (Extended Profile):** complete claims + extended self_profile facets + optional advice/suggestions for the day.
- **L4 (Background):** prompts, config, raw journals for base day ± `--history-days`.

All packs log `meta.token_estimator` (default `char/4.2`), `planned_tokens`, and any trimmed files (`role`, `path`, `reason`). Token counts reuse the shared `_token_estimate` helper so changes to `token_estimator.char_per_token` in `config/config.yaml` stay consistent across persona, index, and pack budgets.
`aijournal pack` now refuses to run until `derived/persona/persona_core.yaml` exists and injects that file at every level (even L2–L4) before layering profile history. If profile/claims files change, the command prints a yellow reminder to re-run `aijournal persona build` so your exported bundles always reflect the latest persona snapshot.

### Retrieval index & filters

```sh
aijournal index rebuild
aijournal index tail
```

- `derived/index/index.db` stores chunk metadata + FTS5 virtual table; `derived/index/annoy.index` stores embeddings; `meta.json` records embedding model/dim/build timestamp and whether fake mode ran.
- Chunking is deterministic (700–1200 chars, sentence boundaries) and each chunk stores `{normalized_id, date, tags, source_type, chunk_index, tokens}`.
- Human-friendly chunk manifests under `derived/index/chunks/YYYY-MM-DD.yaml` (plus optional `.npy` vector shards) mirror the indexed data so you can inspect or reuse it elsewhere, while the built-in retriever expects the Annoy/SQLite artifacts to be present.
- `Retriever.search("question about deep work", k=12, filters=...)` (see `src/aijournal/services/retriever.py`) powers chat/advice and the new `aijournal index search` CLI, combining Annoy cosine scores with a light recency boost. If the index artifacts are missing, retrieval fails fast and prompts you to rebuild.

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
