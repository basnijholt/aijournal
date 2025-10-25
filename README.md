# aijournal

Local-first, YAML-centric personal self-modeling agent. All authoritative data lives in human-readable files; derived artifacts are reproducible via local Ollama. See `PLAN.md` for end-to-end specs, validation details, flows, and commit roadmap.

## Getting Started

```sh
uv sync
uv run pytest -q
```

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

`pack` always includes profile + claims (L1). L2 adds the selected day's normalized entries plus `derived/summaries` and `derived/microfacts` when present. L3 layers on `derived/advice/<date>/*.yaml` and `derived/profile_suggestions/<date>.yaml`—they're optional, so missing files simply drop out. L4 adds every prompt under `prompts/`, the current `config/config.yaml`, and raw `data/journal/YYYY/MM/DD/*.md` files for the base day and any additional days supplied via `--history-days` (history defaults to zero).

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
