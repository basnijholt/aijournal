# aijournal

> [!WARNING]
> This is a mostly AI coded experiment.

**aijournal** is a local-first, AI-powered journal designed to build a deep, evolving understanding of who you are. Imagine a diary that not only stores your thoughts but actively learns from them to maintain a detailed, evidence-linked model of your character—your motivations, goals, values, and ambitions.

Everything is transparent and owned by you, stored in plain text Markdown and YAML files. As you add entries, `aijournal`:

*   **Builds a Living Self-Model:** It continuously distills your writings into a structured "persona core" and a network of typed "claim atoms" about yourself.
*   **Organizes Knowledge in Four Layers (L1→L4):** From a compact persona core to your extended profile, recent activity, and background context, your self-model is organized for any use case.
*   **Grows With You & Stays Current:** A recency-aware scoring algorithm (`strength × impact × decay`) ensures your self-model always reflects the person you are today.
*   **Asks Probing Questions:** The `interview` and `chat` features are tailored to you, helping you reflect, close gaps in self-knowledge, and move toward your goals.
*   **Enables Time Travel:** Because your entire history is backed by Git, you can check out any past commit, regenerate your self-model, and literally ask questions of your younger self.

Runs entirely on your local machine with Ollama. No clouds, no black boxes—just a private, transparent, and reproducible model of you.

## Historical Roadmap

See `docs/archive/PLAN-v0.3.md` for the historical persona-first roadmap (typed claim atoms, retrieval-backed chat, persona core packs).

## Architecture and Design

For a deep dive into core concepts, memory layers, claim atoms, and system internals, read [ARCHITECTURE.md](./ARCHITECTURE.md). Operators running live-mode rehearsals should also consult [agents.md](./agents.md). The CLI now acts mainly as Typer glue; feature-specific orchestration lives in `src/aijournal/commands/`, while testable workflows sit under `src/aijournal/pipelines/`.

## Getting Started

### Quick Workflow Overview

Use the following everyday flow to keep your workspace fresh (see [CLI_MIGRATION.md](CLI_MIGRATION.md) for the full legacy → new command map):

1. `aijournal init` — scaffold a new workspace (idempotent).  
2. `aijournal capture ...` — collect new material (free-form text, `$EDITOR`, files, or whole directories). Capture writes canonical Markdown, records snapshots/manifest rows, and automatically runs normalize → summarize → facts → profile update/review, plus index/persona refreshes as needed.  
3. `aijournal status` — confirm persona/index freshness, pending feedback, and Ollama connectivity.  
4. Use conversational surfaces (`aijournal chat`, `aijournal advise`, `aijournal serve chat`, `aijournal export pack --level ...`) to consume the latest context.

All advanced and one-off utilities now live under `aijournal ops ...` (pipelines, persona/index helpers, feedback). See the migration table below for the old→new mapping.

A quick reminder for live mode: export `AIJOURNAL_OLLAMA_HOST` before running capture or other LLM-backed commands so they don’t fall back to localhost.

A more detailed walkthrough lives in [docs/workflow.md](docs/workflow.md). See [docs/simulator.md](docs/simulator.md) for the human-style simulator and validator harness overview.

```sh
uv sync
uv run pytest -q
```

- Runtime deps beyond Typer/PyYAML/httpx/pydantic/dateutil: `numpy`, `chromadb`, `fastapi`, `uvicorn`, `orjson`. Install once via `uv add ...`; everything stays local-first.
- Retrieval uses Ollama's `embeddinggemma:300m` embeddings by default. Override it with `AIJOURNAL_EMBEDDING_MODEL` env var or by setting `embedding_model` in `config.yaml`; the `AIJOURNAL_MODEL` env var only affects chat/advice, not embeddings.

- `config.yaml` stores runtime defaults (model, host, temperature, advisor settings).
- `src/aijournal/commands/` contains orchestration logic for each Typer command (I/O, retries, progress logging). Most CLI work happens here now; `cli.py` is intentionally thin glue.
- `src/aijournal/pipelines/` hosts deterministic workflows that combine services, prompts, and normalization for a single feature (summaries, facts, persona, packs, profile_update, advise).
- `src/aijournal/models/` defines the Pydantic schemas the CLI enforces on every write.
- `src/aijournal/services/` keeps reusable integrations (Ollama client, retriever, chat daemon, feedback).
- `prompts/*.md` contains the Ollama prompt templates for summarize/facts/profile/advise.
- `profile/` seeds an initial self-profile plus an empty claims list so commands have context.

Run `aijournal init` inside a fresh directory to materialize `data/`, `derived/`, `prompts/`, etc.; repeat executions are idempotent.

### LLM runtime modes

- **Live mode (default):** uses Pydantic AI's Ollama provider via the shared `run_ollama_agent`
 helper. `build_ollama_config_from_mapping` fuses `config.yaml`, environment overrides, and
  per-command tweaks so every CLI surface hits the same configuration pipeline. Populate
  `host: http://192.168.1.143:11434` (or your own endpoint) in `config.yaml` for a
  workspace default; precedence is per-command override → environment
  (`AIJOURNAL_OLLAMA_HOST` or `OLLAMA_BASE_URL`) → config `host` → the
  built-in `http://127.0.0.1:11434`. Model selection follows the same ordering:
  explicit override → `AIJOURNAL_MODEL` → config → default.
  Commands retry schema issues (default 4 attempts via `llm.retries` in `config.yaml` or `--retries` flag) and then fail loudly with an error.
- **Workspace selection:** use the global `--path/-p` option to run against a workspace outside the
  current directory. The default workspace is `Path.cwd()`. Commands validate that the workspace
  contains a `config.yaml` file and provide helpful error messages if initialization is needed.
- **Fake mode (tests/CI):** `export AIJOURNAL_FAKE_OLLAMA=1` to route every agent call through
  deterministic fixtures. This mode must be set explicitly; the CLI never auto-falls back from live mode.

### Claim atoms & persona core

- Claims now live as typed, scoped atoms inside `profile/claims.yaml` with `{type, subject, predicate, value, scope, strength, provenance}` fields.
- `aijournal ops persona build` regenerates `derived/persona/persona_core.yaml` (≤ ~1200 tokens) by selecting top claim atoms + key profile facets. Packs/chat always include this file as L1 context.
- There is intentionally **no** legacy format support—if schema changes, re-run `aijournal init` and regenerate data rather than carrying migration code.
- Use `aijournal ops persona status` anytime you edit `profile/*.yaml` to confirm the cached persona core matches the latest mtimes. The builder now records source file mtimes and `pack` warns when the persona core is stale or missing so you remember to rebuild before sharing context bundles.
- Retrieval, persona, advice, and feedback all run against a strict domain layer in `src/aijournal/domain/`. These `StrictModel` classes back both the CLI and persisted artifacts so serialization bugs surface immediately.

### Strict schemas, artifacts, and migration stance

- Every derived output now persists as an `Artifact[T]` envelope (`kind`, `meta`, `data`). Deterministic helpers in `aijournal/io/artifacts.py` keep the JSON/YAML on disk stable.
- Legacy payloads are gone—commands read and write only the strict envelopes that live under `derived/`.
- Source spans now strip raw text before writing claims or feedback to disk. `aijournal/domain/evidence.py` enforces this at the schema layer and `aijournal ops audit provenance` lets you scan/redact any lingering text when needed.
- Governance hooks (`scripts/check_schemas.py`, JSON schema snapshots under `schemas/core/`, and mandatory pre-commit checks) ensure every schema change is intentional and reviewable.
- Reference outputs under `docs/examples/` showcase the strict persona core, index metadata, microfacts, packs, feedback batches, and chat transcripts generated in fake mode.

## Usage

Fake LLM-powered commands run in fixture mode when `AIJOURNAL_FAKE_OLLAMA=1`:

```sh
export AIJOURNAL_FAKE_OLLAMA=1
```

Most commands below are safe to re-run: they detect unchanged inputs and either skip writes or report what was "already" present. `aijournal capture` dedupes imported content by hash and leaves existing slugs intact, so repeating a command simply reports skipped entries. Fixture generation behind `aijournal ops dev new --fake` behaves similarly by refusing to clobber existing files.

### Initialize the workspace

```sh
aijournal --path ~/journal init
```

Creates the full layout (config/profile/data/derived/prompts). Subsequent runs just print counts of existing directories/files, keeping automation idempotent.

### Capture a new journal entry

```sh
cd ~/journal
aijournal capture --text "Blocked on hiring ops; need to queue backlog." --tags focus
```

Writes a canonical Markdown file under `data/journal/YYYY/MM/DD/<slug>.md`, records a manifest row with the entry hash, stores a raw snapshot (`data/raw/<hash>.md`), and refreshes summaries, micro-facts, profile updates, persona/index, and optional packs in one shot.

You can also pipe raw Markdown straight into the command:

```sh
echo "Journaled from stdin" | aijournal capture --tags daily-log
```

To import existing files or folders:

```sh
aijournal capture --from notes/weekly --source-type notes --tag planning --project roadmap
```

The command recurses for `*.md`/`*.markdown`, dedupes via SHA-256, and logs slug collisions with deterministic `-2`, `-3`, … suffixes.

Capture now supports staged execution so you can stop early or resume manual pipelines:

| Stage | Name                 | What happens                                                         | Manual equivalent |
| ----- | -------------------- | -------------------------------------------------------------------- | ----------------- |
| 0     | persist              | Write canonical Markdown, manifest entry, optional snapshots (adds ≤400 char summary when missing) | handled by capture |
| 1     | normalize            | Emit `data/normalized/YYYY-MM-DD/*.yaml`                             | `uv run aijournal ops pipeline normalize data/journal/YYYY/MM/DD/<entry>.md` |
| 2     | summarize            | Generate `derived/summaries/<date>.yaml`                             | `uv run aijournal ops pipeline summarize --date YYYY-MM-DD` |
| 3     | extract_facts        | Produce micro-facts/claim proposals                                   | `uv run aijournal ops pipeline extract-facts --date YYYY-MM-DD` |
| 4     | profile_update       | Generate profile update batches (optionally auto-apply)               | `uv run aijournal ops profile update --date YYYY-MM-DD` |
| 5     | index_refresh        | Update/rebuild retrieval index                                        | `uv run aijournal ops index update --since 7d` |
| 6     | persona_refresh      | Rebuild persona core when profile data changes                        | `uv run aijournal ops persona build` |
| 7     | pack                 | Emit packs when `--pack` is provided                                  | `uv run aijournal export pack --level Lx [--date YYYY-MM-DD]` |

Use `--max-stage` to stop after a given stage (e.g., `aijournal capture --from notes --max-stage 1` to normalize only). `--min-stage` lets you request a subset of downstream stages, while stages 0–1 always re-run to keep canonical files in sync (existing entries dedupe quickly). After every run the CLI prints any pending stages and the manual commands to finish them if you prefer the classic step-by-step workflow.

### Generate fake entries (fixtures / demos)

```sh
aijournal ops dev new --fake 3 --seed 7 --tags focus planning
```

Produces three Markdown files with full frontmatter (`id`, `created_at`, `title`, `tags`, `projects`, `mood`) plus short body paragraphs. The command never calls Ollama and is safe to run offline; existing slugs are skipped. Provide `--seed` for deterministic fixtures (great for tests/CI) and optionally layer `--tags` to override the auto-generated tag sets.

### Build or tail the retrieval index

```sh
aijournal ops index rebuild               # one-shot rebuild (Chroma chunk index) from normalized YAML
aijournal ops index update --since 7d     # optional helper: follow manifest entries and index new files
aijournal ops index search "deep work ideas" --tags focus --date-from 2025-02-01
```

- Index lives under `derived/index/` (`chroma/`, `meta.json`, `chunks/`).
- Chunking is deterministic (700–1200 characters, sentence boundaries) and every chunk stores normalized_id/date/tags.
- Retrieval relies on a Chroma vector store under `derived/index/chroma`; if those artifacts are missing, commands error loudly so you can rebuild with `aijournal ops index rebuild`.
- `aijournal ops index search` reuses the Retriever service to stream scored snippets with source path/date metadata, honoring `--tags`, `--source`, `--date-from`, and `--date-to` filters.
- FTS5 is a hard requirement: verify with `python - <<'PY'\nimport sqlite3\nprint('fts5' in sqlite3.connect(':memory:').execute(\"pragma compile_options\").fetchall().__str__().lower())\nPY`. If it prints `False`, install an FTS5-enabled SQLite and rebuild Python (macOS: `brew install sqlite` then reinstall Python via `pyenv` or `uv`; Linux: `sudo apt install libsqlite3-dev` before building Python).
- After editing retrieval-related code run `uv run pytest -q` to ensure the CLI and retriever fixtures remain deterministic.

### Ingest existing Markdown (blogs, notes)

Use `aijournal capture --from <path>` for everyday imports. When you need the original ingestion agent
(for scripting or CI), call it via `aijournal ops pipeline ingest`:

```sh
aijournal ops pipeline ingest /home/basnijholt/Work/nijho.lt/content/post --source-type blog
```

Each ingested file is hashed (manifest stored at `data/manifest/ingested.yaml`), a raw snapshot is
saved under `data/raw/<hash>.md`, and normalized YAML lands in `data/normalized/<DATE>/...`. If your
Ollama daemon is listening on a non-default address, set `AIJOURNAL_OLLAMA_HOST` accordingly. Large
directories can take a couple of minutes to process—let the command run to completion or increase
your wrapper's timeout if you're invoking it from automation.

Downstream LLM-backed commands (`summarize`, `facts`, `profile update`) now share a
consistent ergonomics layer: `--progress` surfaces per-entry logging and `--retries` controls
structured-output retries before surfacing an explicit failure. All
of them resolve model/temperature/host via `build_ollama_config_from_mapping` before delegating to
`run_ollama_agent`, so behavior stays aligned across CLI surfaces. Fake mode remains available for
CI/tests by setting `AIJOURNAL_FAKE_OLLAMA=1`.

Advanced reruns are still available through `aijournal ops ...` should you need to drive individual stages manually.

### Normalize Markdown into YAML

```sh
aijournal ops pipeline normalize data/journal/2025/02/03/morning-sync.md
```

Produces `data/normalized/2025-02-03/<entry_id>.yaml`. Files are only rewritten when content changes.

### Summaries

```sh
aijournal ops pipeline summarize --date 2025-02-03
```

Calls `prompts/summarize_day.md` through Ollama and writes `derived/summaries/<DATE>.yaml` as an
`Artifact[DailySummary]` envelope. The payload carries `bullets`, `highlights`, and
`todo_candidates`; model/prompt metadata lives in the artifact `meta`. Set
`AIJOURNAL_FAKE_OLLAMA=1` for deterministic fixtures.

`summarize` (and the other LLM-backed commands) now streams responses through
Pydantic AI's structured output validation. The CLI requests a `DailySummary`
Pydantic model from the LLM and retries schema failures up to `--retries`
times (default 1). Use `--progress` to print each normalized entry before the request is
sent. If the model keeps returning invalid JSON after the configured retries, the
command aborts with an actionable error so you can inspect the upstream output.

> **Why this matters**: The Stage 2 summary is the **starting map** for every
> downstream LLM stage (`extract-facts`, `ops profile update`, and
> `ops profile interview`). Those commands read `SUMMARY_JSON` first and then
> verify claims against normalized entries, so they fail fast with a remediation
> hint when `derived/summaries/<date>.yaml` is missing.

### Micro-facts

```sh
aijournal ops pipeline extract-facts --date 2025-02-03
```

Uses `prompts/extract_facts.md` to create `derived/microfacts/<DATE>.yaml` (an
`Artifact[MicroFactsFile]`) filled with evidence-backed statements. Outputs are
validated against the `MicroFactsFile` model, and fake mode now emits typed
`MicroFact` objects for each entry so the structure matches real runs even in CI.
Each run now also attaches the derived
claim proposals and a consolidation preview: micro-facts are converted into
`ClaimProposal` atoms, pushed through the shared `ClaimConsolidator`, and the
resulting `preview.claim_events` mirror the output of `aijournal ops pipeline review --dry-run`.
Any conflicts are scope-split (weekday vs. weekend, solo vs. team) before falling
back to tentative downgrades, and queued follow-up prompts surface in the CLI so
you can jump straight into `aijournal ops profile interview`.

Regenerate the global consolidated snapshot (and the Chroma index it rides on) at
any time:

```sh
uv run aijournal ops microfacts rebuild
```

This command scans every daily `derived/microfacts/<DATE>.yaml`, deterministically
merges repeated statements, writes `derived/microfacts/consolidated.yaml`, and
records a structured log under `derived/microfacts/logs/` so you have an audit
trail of the rebuild.

> **Heads-up**: The extract-facts stage now depends on the Stage 2 summary for the
> same date. If `derived/summaries/<date>.yaml` is missing, rerun
> `aijournal ops pipeline summarize --date <date>` before extracting facts.

Regenerate the global consolidated snapshot (and the Chroma index it rides on) at
any time:

```sh
uv run aijournal ops microfacts rebuild
```

This command scans every daily `derived/microfacts/<DATE>.yaml`, deterministically
merges repeated statements, writes `derived/microfacts/consolidated.yaml`, and
records a structured log under `derived/microfacts/logs/` so you have an audit
trail of the rebuild.

> **Heads-up**: The extract-facts stage now depends on the Stage 2 summary for the
> same date. If `derived/summaries/<date>.yaml` is missing, rerun
> `aijournal ops pipeline summarize --date <date>` before extracting facts.

Pass `--progress` to watch the entry-by-entry feed and `--retries` to control how many schema failures trigger a
retry. Responses are validated against the `MicroFactsFile` schema; if
validation still fails after the configured retries, the command stops with an
error instead of silently emitting heuristics.

### Ollama health check (fake mode)

```sh
export AIJOURNAL_FAKE_OLLAMA=1
aijournal ops system ollama health
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
aijournal status
aijournal ops profile status
```

The everyday `status` command prints persona/index freshness plus pending updates, while the
`ops profile status` variant dives into facet/claim rankings using the impact weights defined in
`config.yaml`.

### Advisor mode

```sh
aijournal advise "Should I block mornings for focus?"
```

Builds an advice card under `derived/advice/<DATE>/<slug>.yaml` using `prompts/advise.md`, citing the
facets/claims referenced in each recommendation. Fake mode remains available for CI by setting
`AIJOURNAL_FAKE_OLLAMA=1`.

- Advisor Mode now consumes the same interview ranking signal used by `aijournal ops profile interview`, so
  follow-up prompts and scope gaps surface in the assumptions/steps without extra prompting.

### Retrieval-backed chat (CLI)

```sh
aijournal chat "What did I focus on last week?"
```

Streams a short answer grounded in your persona core plus retrieved journal chunks. Each response
includes inline `[entry:<normalized_id>#p<idx>]` citations, a telemetry summary, and—in live mode—an
optional follow-up question that respects `coaching_prefs.probing`. The command exits early when
prerequisites are missing—ensure `derived/persona/persona_core.yaml` and
`derived/index/chroma` exist (rebuild them with `aijournal ops persona build` and
`aijournal ops index rebuild`). Setting `AIJOURNAL_FAKE_OLLAMA=1` keeps the loop deterministic for
tests/CI.

- Use `--session <id>` (or accept the autogenerated `chat-YYYYMMDD-HHMMSS`) to append to a running
  conversation. The turn is archived under `derived/chat_sessions/<session>/` as
  `{transcript.json,summary.yaml,learnings.yaml}` when `--save` is left enabled (default). The
  transcript file is an `Artifact[T]` envelope containing every question/answer turn and telemetry.
- Provide `--feedback up|down` to nudge cited claim strengths (+0.03 / −0.05, clamped 0–1). Each
  feedback event queues a pending update in `derived/pending/profile_updates/feedback_*.yaml`
  summarising the adjustments.
- Toggle `--no-save` for ephemeral answers that should not hit the transcript store.
- Structured telemetry for every turn is emitted to stderr as a compact JSON line (`event:
  chat.telemetry`) so you can tail logs during automation.

> Tip: transcripts, summaries, and learnings are plain JSON/YAML artifacts—keep them under version
> control or ship them into other tooling for audits. The telemetry log makes it easy to aggregate
> response latency and chunk counts in shell pipelines.

### Chat daemon (FastAPI)

```sh
aijournal serve chat --host 127.0.0.1 --port 8080
```

Starts a FastAPI service that reuses the CLI orchestrator. `POST /chat` accepts the same payload
shape (`question`, `top`, optional filters, `session_id`, `feedback`, `save`) and streams
NDJSON frames: a metadata header (telemetry, session, feedback adjustments) followed by the answer
payload. Responses persist to `derived/chat_sessions/` when `save` is true, and claim-feedback
nudges are applied automatically just like the CLI.

Example `curl`:

```sh
curl -N \
  -H 'Content-Type: application/json' \
  -d '{"question": "How did I guard focus yesterday?", "session_id": "focus"}' \
  http://127.0.0.1:8080/chat
```

Use `jq --unbuffered` or similar to stream each NDJSON frame.

### Feedback application

```sh
aijournal ops feedback apply
```

Applies pending feedback batches generated by chat, updates `profile/claims.yaml`, and archives processed files under `derived/pending/profile_updates/applied_feedback/`. The command exits non-zero when no batches were applied so scripts can detect no-op runs.

### Profile update batches

```sh
aijournal ops profile update --date 2025-02-03 --progress
```

Runs `prompts/profile_update.md` with normalized entries, summaries, microfacts,
and the current profile/claims, then emits a `ProfileUpdateBatch` under
`derived/pending/profile_updates/<DATE>-<timestamp>.yaml`. The command uses the
same structured-output path as the other pipelines and enforces the domain
schemas before persisting artifacts.

> **Heads-up**: Profile updates require the same-day summary produced in
> Stage 2. When `derived/summaries/<date>.yaml` is missing, rerun
> `aijournal ops pipeline summarize --date <date>` before calling `ops profile update`.

Use `--progress`, `--timeout`, and `--retries` to mirror the ergonomics of the
other derivations. In fake mode the command still produces deterministic
`ProfileUpdateBatch` envelopes so capture/tests remain consistent.

### Apply profile updates

```sh
aijournal ops profile apply --date 2025-02-03 --yes
```

Applies the most recent pending batch for that date (or a specific
`--file derived/pending/profile_updates/<DATE>-<timestamp>.yaml`) by calling the
same consolidation logic capture uses. Legacy `ProfileUpdateProposals` YAMLs are
still accepted for backwards compatibility, but newly generated artifacts live
exclusively under `derived/pending/profile_updates/`.

### Regenerate persona core (L1)

```sh
aijournal ops persona build
```

Writes `derived/persona/persona_core.yaml` by ranking claim atoms with a
`strength × impact × decay` score, then trimming under the configured token
budget. The persona block keeps the most important facets from
`profile/self_profile.yaml` (values/goals/boundaries/coaching prefs, etc.) plus
the highest ranking claims, and records trimming metadata in
`meta.trimmed`. Override the defaults with `--token-budget`, `--max-claims`, or
`--min-claims` (all mirrored under `config.yaml` → `persona.*`). Token
estimates respect `token_estimator.char_per_token` (default 4.2). The generated
file is always included in packs/chat as the canonical L1 persona core and can
be regenerated safely anytime.

Check whether the cached persona core matches the latest profile edits at any
time:

```sh
aijournal ops persona status
```

The status command compares the recorded mtimes for `profile/*.yaml` against the
current filesystem and prints a yellow reminder (without blocking) when you need
to re-run `persona build`.

### Review pending updates

```sh
aijournal ops pipeline review --apply
```

Lists the latest batch (or the one specified via `--file`) and merges accepted
changes into `profile/` when `--apply` is provided. Use it as a manual approval
step before updating the authoritative self-model.

### Interview probes

```sh
aijournal ops profile interview --date 2025-02-03
```

Ranks facets/claims using impact weights and either (a) generates targeted questions via
`prompts/interview.md` (live mode) or (b) falls back to deterministic heuristics in fake mode.
The command honours `coaching_prefs.probing.max_questions`: set it to `0` to suppress follow-ups,
or tweak the value to control how many concise questions are returned. Structured responses are
validated against the `InterviewSet` schema, and any LLM failure gracefully reverts to the
heuristic probes used previously.

> **Heads-up**: `ops profile interview` also consumes the Stage 2 summary (plus a
> short lookback window). It aborts with the same remediation hint as Stage 3–5
> when `derived/summaries/<date>.yaml` is missing.

### Pack context bundles (L1–L4)

```sh
# Inspect planned files without writing anything
aijournal export pack --level L2 --dry-run

# Persist a reusable bundle (rewrites only when content changes)
aijournal export pack --level L1 --output derived/packs/l1.yaml

# Include advice + profile updates (optional) in an L3 pack
aijournal export pack --level L3 --date 2025-02-03 --max-tokens 2800

# L4 with 2 days of history, prompts, config, and raw journals
aijournal export pack --level L4 --date 2025-02-03 --history-days 2 --dry-run

# Emit an L4 pack as JSON for piping into another tool
aijournal export pack --level L4 --date 2025-02-03 --history-days 1 --format json > /tmp/context-l4.json
```

`pack` now follows the standardized layers:

- **L1 (Persona Core):** `derived/persona/persona_core.yaml` + top accepted claim atoms.
- **L2 (Recent Activity):** today's normalized entries plus the most recent 7 summaries/micro-facts.
- **L3 (Extended Profile):** complete claims + extended self_profile facets + optional advice/suggestions for the day.
- **L4 (Background):** prompts, config, raw journals for base day ± `--history-days`.

All packs log `meta.token_estimator` (default `char/4.2`), `planned_tokens`, and any trimmed files (`role`, `path`, `reason`). Token counts reuse the shared `_token_estimate` helper so changes to `token_estimator.char_per_token` in `config.yaml` stay consistent across persona, index, and pack budgets.

Default token budgets are optimized for LLM context windows:
- **L1:** 1200 tokens (persona core only)
- **L2:** 2000 tokens (persona + recent summaries)
- **L3:** 2600 tokens (persona + summaries + facts)
- **L4:** 3200 tokens (persona + full context)

For sharing full context with external LLMs, use `--max-tokens` to increase the budget and reduce trimming:

```sh
# Full context export with minimal trimming (useful for sharing with other tools)
aijournal export pack --level L4 --max-tokens 20000 --format yaml
```

`aijournal export pack` now refuses to run until `derived/persona/persona_core.yaml` exists and injects that file at every level (even L2–L4) before layering profile history. If profile/claims files change, the command prints a yellow reminder to re-run `aijournal ops persona build` so your exported bundles always reflect the latest persona snapshot.

### Retrieval index & filters

```sh
aijournal ops index rebuild
aijournal ops index update
```

- `derived/index/chroma/` stores chunk metadata plus vectors; `meta.json` records embedding model/dim/build timestamp and whether fake mode ran.
- Chunking is deterministic (700–1200 chars, sentence boundaries) and each chunk stores `{normalized_id, date, tags, source_type, chunk_index, tokens}`.
- Human-friendly chunk artifacts (`ArtifactKind.INDEX_CHUNKS`) under `derived/index/chunks/YYYY-MM-DD.yaml` wrap a `ChunkBatch` payload (day + chunk list) so you can inspect or reuse the indexed data. Matching `.npy` shards store the raw vectors. The built-in retriever depends on the persisted Chroma collection and these manifests for auditing.
- `Retriever.search("question about deep work", k=12, filters=...)` (see `src/aijournal/services/retriever.py`) powers chat/advice and the `aijournal ops index search` CLI, combining Chroma similarity scores with a light recency boost. If the index artifacts are missing, retrieval fails fast and prompts you to rebuild.

### Configuration quick reference

`config.yaml` ships with defaults for the chat/advice model, temperature, seed, impact weights, token estimator, and persona budgets. You can optionally add:

- `embedding_model: "<model-name>"` to change the embedding model (defaults to `embeddinggemma:300m` when omitted). Can also be overridden with `AIJOURNAL_EMBEDDING_MODEL` env var.
- `llm: {retries: 4, timeout: 120.0}` to customize LLM retry behavior and request timeouts.
- `index: {search_k_factor: 3.0}` to tweak retrieval fan-out.
- `chat: {max_retrieved_chunks: 12, max_claims: 16, follow_up_enabled: true, write_back_facts: true}` for retrieval/chat behaviour.
- Custom `impact_weights.claim_types` if certain claim types should rank higher.
- Adjusted `persona.{token_budget,max_claims,min_claims}` if you need a larger or smaller persona core.

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

## Contributing

Interested in helping out? Follow the setup and quality guidelines in [CONTRIBUTING.md](./CONTRIBUTING.md).
