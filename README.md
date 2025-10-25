# aijournal

Local-first, YAML-centric personal self-modeling agent. All authoritative data lives in human-readable files; derived artifacts are reproducible via local Ollama. See `PLAN.md` for end-to-end specs, schemas, flows, and commit roadmap.

## Getting Started

```sh
uv sync
uv run pytest -q
```

Key directories will be created by `aijournal init` in future commits. For now, see `config/` for defaults and `profile/` for the seeded self profile and claims scaffold.

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

### Summaries (fake Ollama)

```sh
AIJOURNAL_FAKE_OLLAMA=1 aijournal summarize --date 2025-02-03
```

Generates `derived/summaries/2025-02-03.yaml`. Without the env var, the command exits until real Ollama support ships.

### Micro-facts (fake Ollama)

```sh
AIJOURNAL_FAKE_OLLAMA=1 aijournal facts --date 2025-02-03
```

Creates `derived/microfacts/2025-02-03.yaml` with placeholder facts. Idempotent writes prevent churn.

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

### Advisor mode (fake Ollama)

```sh
AIJOURNAL_FAKE_OLLAMA=1 aijournal advise "Should I block mornings for focus?"
```

Stores an advice card under `derived/advice/<DATE>/<slug>.yaml` and prints the path.

### Profile suggestions (fake Ollama)

```sh
AIJOURNAL_FAKE_OLLAMA=1 aijournal profile suggest --date 2025-02-03
```

Writes `derived/profile_suggestions/2025-02-03.yaml`, summarizing proposed upserts/updates.

### Apply profile suggestions

```sh
aijournal profile apply --date 2025-02-03 --yes
```

Applies the derived suggestions into `profile/self_profile.yaml` and `profile/claims.yaml`, updating `last_updated` stamps only when something changes.

### Pack context bundles (L1–L4)

```sh
# Inspect planned files without writing anything
aijournal pack --level L2 --dry-run

# Persist a reusable bundle (rewrites only when content changes)
aijournal pack --level L1 --output derived/packs/l1.yaml

# Include advice + profile suggestions (optional) in an L3 pack
AIJOURNAL_FAKE_OLLAMA=1 aijournal pack --level L3 --date 2025-02-03 --max-tokens 2800

# L4 with 2 days of history, prompts, config, and raw journals
aijournal pack --level L4 --date 2025-02-03 --history-days 2 --dry-run

# Emit an L4 pack as JSON for piping into another tool
aijournal pack --level L4 --date 2025-02-03 --history-days 1 --format json > /tmp/context-l4.json
```

`pack` always includes profile + claims (L1). L2 adds the selected day's normalized entries plus `derived/summaries` and `derived/microfacts` when present. L3 layers on `derived/advice/<date>/*.yaml` and `derived/profile_suggestions/<date>.yaml`—they're optional, so missing files simply drop out. L4 adds every prompt under `prompts/`, the current `config/config.yaml`, and raw `data/journal/YYYY/MM/DD/*.md` files for the base day and any additional days supplied via `--history-days` (history defaults to zero).

Trimming now prioritizes raw journal content first; when a pack exceeds `--max-tokens`, entries are zeroed in deterministic role order and `meta.trimmed` captures a list of `{role, path}` objects so you can inspect exactly what was removed. Dry-run output still lists every planned file with its token estimate, and both YAML/JSON payloads remain deterministic for caching or scripting.

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
