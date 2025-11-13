# aijournal CLI & Pipeline Inventory

**Purpose**: Complete system enumeration for invariant testing and human-style simulator design.

**Generated**: 2025-11-12
**Scope**: All CLI entrypoints, pipeline stages, artifacts, and data flows

---

## 1. CLI Command Hierarchy

### 1.1 Top-Level Commands

| Command | Module | Description | Side Effects |
|---------|--------|-------------|--------------|
| `init` | `commands/init.py` | Initialize workspace layout | Creates dirs/files under workspace |
| `capture` | `services/capture/__init__.py` | Ingest & run pipeline (0-8 stages) | Writes journal, manifest, raw, normalized, runs derivations |
| `status` | `commands/system.py` | Display workspace health | None (read-only) |
| `chat` | `commands/chat.py` | RAG-backed chat session | Writes transcripts to `derived/chat_sessions/`, optional feedback batches |
| `advise` | `commands/advise.py` | Generate advice from profile | Writes advice cards to `derived/advice/` |

### 1.2 Export Commands (`export.*`)

| Command | Module | Description | Side Effects |
|---------|--------|-------------|--------------|
| `export pack` | `commands/pack.py` | Assemble context bundles L1-L4 | Writes packs to `derived/packs/` or stdout |

### 1.3 Serve Commands (`serve.*`)

| Command | Module | Description | Side Effects |
|---------|--------|-------------|--------------|
| `serve chat` | `commands/chatd.py` | FastAPI chat daemon | Writes session transcripts, feedback batches |

### 1.4 Ops Commands (`ops.*`)

#### ops.pipeline

| Command | Module | Description | Side Effects |
|---------|--------|-------------|--------------|
| `ops pipeline normalize` | `cli.py:normalize` | Normalize single entry | Writes `data/normalized/YYYY-MM-DD/<id>.yaml` |
| `ops pipeline summarize` | `commands/summarize.py` | Summarize day | Writes `derived/summaries/<date>.yaml` |
| `ops pipeline extract-facts` | `commands/facts.py` | Extract facts | Writes `derived/microfacts/<date>.yaml` |
| `ops pipeline characterize` | `commands/characterize.py` | Derive profile updates | Writes `derived/pending/profile_updates/<batch>.yaml` |
| `ops pipeline review` | `cli.py:review_updates` | Review/apply batches | Updates `profile/self_profile.yaml`, `profile/claims.yaml` |
| `ops pipeline ingest` (legacy) | `commands/ingest.py` | Legacy ingest | Writes journal, normalized (deprecated) |

#### ops.profile

| Command | Module | Description | Side Effects |
|---------|--------|-------------|--------------|
| `ops profile suggest` | `commands/profile.py` | Generate suggestions | Writes `derived/profile_proposals/<date>.yaml` |
| `ops profile apply` | `commands/profile.py` | Apply suggestions | Updates `profile/self_profile.yaml`, `profile/claims.yaml` |
| `ops profile status` | `commands/profile.py` | Show review priorities | None (read-only) |
| `ops profile interview` | `cli.py:interview` | Generate interview probes | None (stdout only) |

#### ops.index

| Command | Module | Description | Side Effects |
|---------|--------|-------------|--------------|
| `ops index rebuild` | `commands/index.py` | Rebuild full index | Writes `derived/index/index.db`, `annoy.index`, `meta.json`, `chunks/` |
| `ops index update` | `commands/index.py` | Incremental index refresh | Updates SQLite/Annoy, writes new chunks |
| `ops index search` | `commands/index.py` | Search index | None (read-only) |

#### ops.persona

| Command | Module | Description | Side Effects |
|---------|--------|-------------|--------------|
| `ops persona build` | `commands/persona.py` | Regenerate persona core | Writes `derived/persona/persona_core.yaml` |
| `ops persona status` | `commands/persona.py` | Check persona freshness | None (read-only) |

#### ops.feedback

| Command | Module | Description | Side Effects |
|---------|--------|-------------|--------------|
| `ops feedback apply` | `cli.py:feedback_apply` | Apply chat feedback batches | Updates `profile/claims.yaml`, archives batches |

#### ops.system

| Command | Module | Description | Side Effects |
|---------|--------|-------------|--------------|
| `ops system doctor` | `commands/system.py` | Run diagnostics | None (read-only) |
| `ops system ollama health` | `cli.py:ollama_health` | Check Ollama connectivity | None (read-only) |

#### ops.audit

| Command | Module | Description | Side Effects |
|---------|--------|-------------|--------------|
| `ops audit provenance` | `commands/audit.py` | Scan/fix span.text remnants | Optional: redacts text in claims/artifacts with `--fix` |

#### ops.logs

| Command | Module | Description | Side Effects |
|---------|--------|-------------|--------------|
| `ops logs tail` | `cli.py:logs_tail` | Show recent trace events | None (read-only) |

#### ops.dev

| Command | Module | Description | Side Effects |
|---------|--------|-------------|--------------|
| `ops dev new` | `commands/new.py` | Generate fake entries (test fixture) | Writes fake journal entries |

---

## 2. Capture Pipeline Stages (0-8)

The `capture` command orchestrates a 9-stage pipeline. Each stage is conditional and can be filtered with `--min-stage`/`--max-stage`.

| Stage | Name | Module | Inputs | Outputs | Side Effects |
|-------|------|--------|--------|---------|--------------|
| **0** | `persist` | `services/capture/stages/stage0_persist.py` | `CaptureInput` (text/paths) | Canonical Markdown, manifest entries | Writes `data/journal/YYYY/MM/DD/<slug>.md`, `data/raw/<hash>.md`, updates `data/manifest/ingested.yaml` |
| **1** | `normalize` | `services/capture/stages/stage1_normalize.py` | EntryResults from stage 0 | Normalized YAML | Writes `data/normalized/YYYY-MM-DD/<id>.yaml` |
| **2** | `summarize` | `services/capture/stages/stage2_summarize.py` | Changed dates | Daily summaries | Writes `derived/summaries/<date>.yaml` |
| **3** | `extract_facts` | `services/capture/stages/stage3_facts.py` | Changed dates | Micro-facts + claim proposals | Writes `derived/microfacts/<date>.yaml` |
| **4** | `profile_update` | `services/capture/stages/stage4_profile.py` | Changed dates | Profile suggestions, optional apply | Writes `derived/profile_proposals/<date>.yaml`, optionally updates `profile/*` |
| **5** | `characterize_review` | `services/capture/stages/stage5_characterize.py` | Changed dates | Pending batches, optional review | Writes `derived/pending/profile_updates/<batch>.yaml`, optionally updates `profile/*` |
| **6** | `index_refresh` | `services/capture/stages/stage6_index.py` | Changed dates | Updated retrieval index | Updates `derived/index/index.db`, `annoy.index`, `meta.json`, writes `chunks/` |
| **7** | `persona_refresh` | `services/capture/stages/stage7_persona.py` | Artifact change map | Persona core | Writes `derived/persona/persona_core.yaml` |
| **8** | `pack` | `services/capture/stages/stage8_pack.py` | `CaptureInput.pack` level | Context packs | Writes `derived/packs/<level>_<date>.yaml` or `.json` |

### Stage Dependencies

```
stage 0 (persist) → stage 1 (normalize)
                      ↓
    ┌─────────────────┴─────────────────┐
    ↓                 ↓                  ↓
stage 2 (summarize)   stage 3 (facts)    stage 4 (profile)
                                         ↓
                                    stage 5 (characterize)
                                         ↓
    ┌────────────────┬─────────────────┴──────────┐
    ↓                ↓                             ↓
stage 6 (index)   stage 7 (persona)           stage 8 (pack)
```

**Key Contracts**:
- Stages 0-1 always run together (validation requirement)
- Stages 2-5 only execute when `changed_dates` is non-empty
- Stage 6-7 controlled by `--rebuild` flag (`auto`/`always`/`skip`)
- Stage 8 only runs when `--pack` is specified

---

## 3. File System Artifacts

### 3.1 Directory Structure

#### Authoritative (version-controlled, human-edited)
```
data/
  journal/YYYY/MM/DD/*.md       # Canonical journal Markdown
  normalized/YYYY-MM-DD/*.yaml  # Structured entry YAML (was derived, now authoritative)
  raw/<hash>.md                 # Raw snapshots (import dedupe)
  manifest/ingested.yaml        # SHA-256 index, source tracking

profile/
  self_profile.yaml             # Faceted self-model (traits, values, goals, boundaries)
  claims.yaml                   # Typed claim atoms with provenance

prompts/*.md                    # LLM structured-output templates

config.yaml                     # Workspace config (model, host, weights, budgets)
```

#### Derived (reproducible, safe to delete)
```
derived/
  summaries/<date>.yaml            # Daily summary (bullets, highlights, TODOs)
  microfacts/<date>.yaml           # Micro-facts + claim proposals
  profile_proposals/<date>.yaml    # Profile suggestions from daily processing
  pending/
    profile_updates/<batch>.yaml   # Pending characterization batches
    profile_updates/applied_feedback/*.yaml  # Archived feedback batches

  persona/persona_core.yaml        # L1 bundle: top facets + claims (token-trimmed)

  index/
    index.db                       # SQLite FTS5 (chunks + metadata)
    annoy.index                    # Annoy vector index
    meta.json                      # Index metadata (embedding dim, trees, search_k)
    chunks/YYYY-MM-DD.yaml         # Chunk manifests (ChunkBatch artifacts)

  chat_sessions/<session>/
    <turn_id>.transcript.yaml      # Chat turn with question, answer, citations
    <turn_id>.telemetry.yaml       # Timing, token counts, retrieval stats
    <turn_id>.feedback.yaml        # Feedback batch (strength adjustments)

  advice/<timestamp>.yaml          # Advice cards with recommendations

  packs/
    L1_<date>.yaml                 # L1 persona pack
    L3_<date>.yaml                 # L3 full profile pack
    L4_<date>.yaml                 # L4 background pack

  logs/
    run_trace.jsonl                # Global structured trace log
    capture/<run_id>.jsonl         # Per-capture telemetry
    capture/<run_id>.result.json   # Capture run summary
```

### 3.2 Artifact Schema Patterns

All derived outputs wrap data in `Artifact[T]` envelopes:

```yaml
kind: <ArtifactKind enum>
meta:
  llm_model: "gpt-oss:20b"
  prompt_path: "prompts/summarize_day.md"
  prompt_hash: "<sha256>"
  created_at: "2025-11-12T10:30:00Z"
  manifest_hashes: ["<sha256>", ...]  # optional: links to source material
  trimmed: {...}                       # optional: trimming metadata for packs
data:
  <schema-specific payload>
```

**ArtifactKind Values** (domain/enums.py):
- `DAILY_SUMMARY`, `MICROFACTS`, `PROFILE_UPDATE_PROPOSALS`, `PROFILE_UPDATE_BATCH`
- `PERSONA_CORE`, `ADVICE_CARD`, `INTERVIEW_SET`
- `CHAT_TRANSCRIPT`, `CHAT_TELEMETRY`, `FEEDBACK_BATCH`
- `INDEX_META`, `INDEX_CHUNKS`
- `CONTEXT_PACK_L1`, `CONTEXT_PACK_L2`, `CONTEXT_PACK_L3`, `CONTEXT_PACK_L4`

---

## 4. Database Interactions

### 4.1 SQLite (`derived/index/index.db`)

**Tables**:
- `chunks` (FTS5 virtual table):
  - `rowid` (INTEGER PRIMARY KEY)
  - `entry_id` TEXT
  - `chunk_index` INTEGER
  - `section_heading` TEXT
  - `text` TEXT (indexed for full-text search)
  - `embedding_json` TEXT (serialized vector)
  - `date` TEXT (YYYY-MM-DD)
  - `tags_json` TEXT (JSON array)
  - `source_type` TEXT
  - `created_at` TEXT (ISO timestamp)

**Indexes**:
- FTS5 built-in text index
- Additional indexes on `date`, `entry_id`, `source_type` for filtering

**Operations**:
- `INSERT` during index rebuild/update
- `SELECT` with FTS5 `MATCH` for keyword search + metadata filters
- `DELETE` during full rebuild (drop/recreate table)

### 4.2 Annoy Index (`derived/index/annoy.index`)

**Format**: Binary Annoy index (angular metric)

**Operations**:
- Build: `AnnoyIndex(embedding_dim, 'angular')` → `add_item(rowid, vector)` → `build(n_trees)`
- Search: `get_nns_by_vector(query_vec, k, search_k=search_k_factor * k * n_trees)`

**Metadata**: `derived/index/meta.json`
```json
{
  "embedding_dim": 300,
  "ann_trees": 50,
  "search_k_factor": 3.0,
  "total_chunks": 1234,
  "created_at": "2025-11-12T10:30:00Z",
  "fake_mode": false
}
```

---

## 5. Core Services & Utilities

### 5.1 Services (`src/aijournal/services/`)

| Module | Purpose | Key Functions | Dependencies |
|--------|---------|---------------|--------------|
| `ollama.py` | LLM client | `run_ollama_agent`, `build_ollama_config_from_mapping` | httpx, instructor |
| `embedding.py` | Vector generation | `embed_texts` | Ollama API |
| `retriever.py` | ANN + metadata search | `Retriever.search` | SQLite, Annoy |
| `consolidator.py` | Claim merging | `ClaimConsolidator.upsert` | normalization |
| `chat.py` | Chat orchestrator | `run_chat_turn` | retriever, ollama |
| `chat_api.py` | FastAPI streaming | `ChatAPIService.stream` | chat.py |
| `feedback.py` | Feedback batches | `apply_feedback` | claims.yaml |
| `capture/__init__.py` | Pipeline orchestrator | `run_capture` | All stage modules |

### 5.2 Pipelines (`src/aijournal/pipelines/`)

| Module | Purpose | Key Functions | Inputs | Outputs |
|--------|---------|---------------|--------|---------|
| `normalization.py` | Data normalization | `normalize_claim_atom`, `normalize_created_at` | Raw dicts/models | Validated domain models |
| `summarize.py` | Summary generation | `generate_summary` | NormalizedEntry[] | DailySummary |
| `facts.py` | Facts extraction | `generate_microfacts` | NormalizedEntry[] | MicroFactsFile |
| `characterize.py` | Characterization | (delegates to command) | NormalizedEntry[] | ProfileUpdateBatch |
| `advise.py` | Advice generation | (delegates to command) | Profile + question | AdviceCard |
| `persona.py` | Persona builder | `build_persona_core` | Profile + claims | PersonaCore |
| `pack.py` | Pack assembly | `build_pack` | Level, date, history | ContextPack |
| `index.py` | Index builder | `rebuild_index` | Normalized entries | SQLite + Annoy |

### 5.3 Domain Models (`src/aijournal/domain/`)

Core domain entities (all inherit `StrictModel`):

- **journal.py**: `NormalizedEntry`, `Section`
- **claims.py**: `ClaimAtom`, `ClaimSource`, `ClaimSourceSpan`, `Scope`, `Provenance`
- **facts.py**: `MicroFact`, `DailySummary`, `MicroFactsFile`
- **changes.py**: `ClaimProposal`, `FacetChange`, `ProfileUpdateProposals`, `ProfileUpdateBatch`
- **persona.py**: `PersonaCore`, `InterviewQuestion`, `InterviewSet`
- **evidence.py**: `SourceRef`, `redact_source_text`
- **events.py**: `ClaimPreviewEvent`, `FeedbackBatch`, `ChatTelemetry`
- **enums.py**: `ArtifactKind`, `ClaimType`, `ClaimMethod`, `ClaimStatus`

### 5.4 Utilities (`src/aijournal/utils/`)

| Module | Purpose | Key Functions |
|--------|---------|---------------|
| `paths.py` | Path resolution | `normalized_entry_path`, `resolve_path`, directory constants |
| `time.py` | Timestamp handling | `format_timestamp`, `created_date`, `slugify_title` |
| `coercion.py` | Type coercion | `coerce_int`, `coerce_float` |

---

## 6. Implicit Assumptions & Contracts

### 6.1 Workspace Invariants

1. **config.yaml must exist** at workspace root (validated by `_get_workspace()`)
2. **Authoritative dirs** are never deleted by the system
3. **Derived dirs** can be safely deleted and rebuilt
4. **Stage 0-1** always execute together when capture runs
5. **Manifest** dedupe by SHA-256 prevents re-ingestion

### 6.2 Data Flow Assumptions

1. **Normalized entries** are the authoritative source for all derivations
2. **Claim atoms** always carry provenance (sources + timestamps)
3. **Persona core** must be fresh before running chat/advise/pack
4. **Index** must exist before retrieval (explicit error if missing)
5. **Feedback batches** reference claims by ID (unmatched claims → warning)

### 6.3 LLM Contracts

1. **Structured outputs** are validated against Pydantic models
2. **Retries** default to 4 attempts with exponential backoff (configurable via `llm.retries`)
3. **Fake mode** (`AIJOURNAL_FAKE_OLLAMA=1`) bypasses network calls with deterministic fixtures
4. **Prompt hashing** ensures reproducibility (changing prompt invalidates derived artifacts)

### 6.4 Concurrency & Safety

1. **Single-writer assumption**: One capture process at a time (no file locking)
2. **Read-only commands** safe to run concurrently (`status`, `search`, `chat` with `--no-save`)
3. **SQLite check_same_thread=False** for chatd multi-threading
4. **No distributed state**: All operations are local filesystem

### 6.5 Error Handling Patterns

1. **Validation failures** → log warning, skip item, continue processing
2. **LLM timeouts** → retry up to `--retries`, fallback to deterministic output if possible
3. **Missing index** → explicit error with recovery command
4. **Stale persona** → warning + suggest rebuild
5. **Capture errors** accumulate in `CaptureResult.errors[]` but don't halt pipeline

---

## 7. Stage Input/Output Contracts

### Stage 0: persist
**Inputs**:
- `CaptureInput` (source: stdin/editor/file/dir, text, paths, metadata)
- Workspace root, config, manifest_entries list (mutated)

**Outputs**:
- `PersistStage0Outputs(entries: List[EntryResult], result: OperationResult, duration_ms: float)`
- **Side Effects**: Writes journal MD, raw snapshots, updates manifest

**Entry Point**: `run_persist_stage_0(inputs, root, config, manifest_entries, log_event)`

### Stage 1: normalize
**Inputs**:
- `List[EntryResult]` from stage 0
- Workspace root, config

**Outputs**:
- `NormalizeStageOutputs(artifacts: dict, result: OperationResult, duration_ms: float, changed_dates: List[str])`
- **Side Effects**: Writes normalized YAML files

**Entry Point**: `run_normalize_stage_1(entry_results, root, config)`

### Stage 2: summarize
**Inputs**:
- `changed_dates: List[str]`
- `CaptureInput` (for retries, progress flags)
- Workspace root

**Outputs**:
- `SummarizeStage2Outputs(result: OperationResult, duration_ms: float, paths: List[str])`
- **Side Effects**: Writes summary YAML artifacts

**Entry Point**: `run_summarize_stage_2(changed_dates, inputs, root)`

### Stage 3: extract_facts
**Inputs**:
- `changed_dates: List[str]`
- `CaptureInput` (for retries, progress flags)
- Workspace root, config

**Outputs**:
- `FactsStage3Outputs(result: OperationResult, duration_ms: float, paths: List[str])`
- **Side Effects**: Writes microfacts YAML artifacts

**Entry Point**: `run_facts_stage_3(changed_dates, inputs, root, config)`

### Stage 4: profile_update
**Inputs**:
- `changed_dates: List[str]`
- `CaptureInput` (apply_profile flag, retries)
- Workspace root, config

**Outputs**:
- `ProfileStage4Outputs(suggest_result, apply_result, duration_ms, suggestion_paths, applied_count)`
- **Side Effects**: Writes proposals, optionally updates profile/claims

**Entry Point**: `run_profile_stage_4(changed_dates, inputs, root, config)`

### Stage 5: characterize_review
**Inputs**:
- `changed_dates: List[str]`
- `CaptureInput` (apply_profile flag)
- Workspace root, config

**Outputs**:
- `CharacterizeStage5Outputs(result, review_result, duration_ms, new_batches, applied_batches, pending_batches, review_candidates)`
- **Side Effects**: Writes pending batches, optionally updates profile/claims, archives applied batches

**Entry Point**: `run_characterize_stage_5(changed_dates, inputs, root, config)`

### Stage 6: index_refresh
**Inputs**:
- `changed_dates: List[str]`
- Workspace root
- `rebuild: str` ("auto", "always", "skip")

**Outputs**:
- `IndexStage6Outputs(result, duration_ms, updated, rebuilt)`
- **Side Effects**: Updates/rebuilds SQLite + Annoy index, writes chunks

**Entry Point**: `run_index_stage_6(changed_dates, root, rebuild)`

### Stage 7: persona_refresh
**Inputs**:
- `CaptureInput` (rebuild flag)
- Workspace root, config
- `artifacts_changed: dict` (determines if rebuild needed)

**Outputs**:
- `PersonaStage7Outputs(result, duration_ms, persona_changed, persona_stale_before/after, status_before/after, error)`
- **Side Effects**: Writes persona_core.yaml

**Entry Point**: `run_persona_stage_7(inputs, root, config, artifacts_changed)`

### Stage 8: pack
**Inputs**:
- `CaptureInput` (pack level: L1/L3/L4 or None)
- Workspace root
- `run_id: str` (for output naming)
- `persona_changed: bool` (triggers rebuild)

**Outputs**:
- `PackStage8Outputs(result, duration_ms)`
- **Side Effects**: Writes pack YAML/JSON to derived/packs/

**Entry Point**: `run_pack_stage_8(inputs, root, run_id, persona_changed)`

---

## 8. Testing Invariants (Human Simulator Focus)

### What a Human Simulator Should Verify

1. **Stage Execution Order**: Stages 2-5 only run when `changed_dates` exists
2. **File Consistency**: Every normalized entry has a matching journal MD source
3. **Manifest Dedupe**: Ingesting same file twice → second ingestion is deduped
4. **Claim Provenance**: All claims reference valid entry IDs in sources
5. **Persona Freshness**: Chat/advise fail or warn when persona is stale
6. **Index Rebuild**: Deleting index.db → rebuild creates matching chunk count
7. **Feedback Application**: Strength adjustments clamp to [0, 1] and preserve claim IDs
8. **Telemetry**: Every capture run writes `<run_id>.jsonl` with stage events
9. **Pack Determinism**: Same inputs → same pack output (given frozen timestamps)
10. **Error Accumulation**: Capture continues after LLM failures, accumulates warnings

### Simulation Scenarios

**Scenario 1: First-time setup**
```
init → capture --text "kickoff" → status
Expected: All dirs created, 1 entry, normalized, summarized, persona built, index created
```

**Scenario 2: Re-ingestion dedupe**
```
capture --from notes/file.md → capture --from notes/file.md (again)
Expected: Second capture shows deduped=True, no downstream work
```

**Scenario 3: Incremental capture**
```
capture --text "day 1" → capture --text "day 2" (different date)
Expected: Two distinct normalized entries, summaries, index updated (not rebuilt)
```

**Scenario 4: Profile update cascade**
```
capture --text "I love deep work" → ops profile status → chat "What motivates me?"
Expected: Claim atom created, persona refreshed, chat cites new claim
```

**Scenario 5: Feedback loop**
```
chat "..." --feedback down → ops feedback apply
Expected: Claim strength decreases, feedback batch archived
```

**Scenario 6: Stage filtering**
```
capture --from file.md --max-stage 1 → capture --from file.md --min-stage 2
Expected: First run persists+normalizes only, second run derives from normalized
```

---

## 9. Summary & Next Steps

This inventory provides the complete map of:
- **45+ CLI commands** across 9 namespaces
- **9 capture pipeline stages** with explicit I/O contracts
- **50+ file artifacts** across authoritative and derived layers
- **2 databases** (SQLite FTS5 + Annoy ANN)
- **Core services** (ollama, retriever, consolidator, chat, capture orchestrator)
- **Domain models** (20+ strict schemas)
- **Implicit assumptions** (workspace layout, single-writer, prompt hashing)

**For Human Simulator Design**:
1. Use stage contracts (§7) to define valid execution sequences
2. Verify file existence patterns against directory structure (§3)
3. Test claim consolidation logic with scope conflicts (§5.1)
4. Validate SQLite/Annoy consistency after index operations (§4)
5. Simulate error recovery (LLM timeout, missing index, stale persona) per §6.5

**For Invariant Testing**:
- Cross-reference manifest hashes against raw snapshots
- Verify persona_core token budgets match config
- Ensure feedback batches archive after application
- Check telemetry completeness (all stages emit events)
- Validate artifact envelope `meta` fields (prompt_hash, created_at, etc.)

**Known Gaps**:
- Real-time concurrency behavior (single-writer assumption not enforced)
- Network failure modes (Ollama unreachable → partial failures)
- Disk space exhaustion (no pre-flight checks)

---

**End of Inventory**
