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

### Pack context bundles (L1/L2)

```sh
# Inspect planned files without writing anything
aijournal pack --level L2 --dry-run

# Persist a reusable bundle (rewrites only when content changes)
aijournal pack --level L1 --output derived/packs/l1.yaml

# Emit JSON to stdout with explicit date + budget
aijournal pack --level L2 --date 2025-02-03 --max-tokens 1800 --format json > /tmp/context.json
```

`pack` always includes profile + claims (L1) and, for L2, the selected day's normalized entries plus any derived summaries/microfacts it finds. Dry-run output documents token counts, and specifying `--output` keeps the command idempotent for automation.

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
