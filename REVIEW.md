# REVIEW

Comprehensive technical and scientific review of the `aijournal` repository as inspected on **October 30, 2025**. This document is self-contained: it captures the current implementation state, highlights gaps surfaced by external prompt feedback, and fuses the theoretical validation plan discussed earlier. Treat it as the canonical reference going forward.

## 1. Repository Baseline
- **Branch/Status:** Local branch `data-models` with upstream `origin/data-models`. Working tree is clean aside from this document (`git status -sb` shows `?? REVIEW.md`).
- **Toolchain:** Python 3.11+, `uv` for dependency & env management, Typer CLI (`src/aijournal/cli.py`). All runtime commands should be executed via `uv run ...` per `CONTRIBUTING.md`. Pre-commit hooks (Ruff, mypy, schema checks) are wired through `.githooks/pre-push` and `scripts/check_schemas.py`.
- **LLM runtime:** `src/aijournal/services/ollama.py` wraps Pydantic AI’s Ollama provider. Default config targets `gpt-oss:20b` at `http://127.0.0.1:11434` unless overridden by `AIJOURNAL_MODEL` / `AIJOURNAL_OLLAMA_HOST` env vars or `config.yaml`.

## 2. System Architecture (Grounded in Code)
### 2.1 Directory Layout
`ARCHITECTURE.md` lists the high-level tree, confirmed by local inspection:

```
aijournal/
  README.md                     # product overview
  docs/workflow.md              # operational guide
  docs/archive/PLAN-v0.3.md     # historical plan
  src/aijournal/
    cli.py                      # Typer entry point
    commands/                   # orchestration per surface
    services/                   # Ollama, capture, retriever, etc.
    pipelines/                  # deterministic workflows (summaries, facts, persona…)
    domain/                     # StrictModel domain layer (claims, evidence, facts…)
    models/                     # Authoritative + derived Pydantic models
    io/                         # artifact helpers (YAML/JSON envelopes)
  prompts/                      # LLM prompt templates
  profile/                      # authoritative self-profile & claims
  data/                         # canonical journals/normalized entries
  derived/                      # reproducible artifacts (summaries, microfacts, persona…)
```

### 2.2 Capture-First Pipeline
The capture orchestrator (`src/aijournal/services/capture/__init__.py`) defines the end-to-end pipeline via `CAPTURE_STAGES`:

```python
CAPTURE_STAGES: list[CaptureStage] = [
    CaptureStage(0, "persist", "Write canonical Markdown, update manifest, and store optional raw snapshots.", ...),
    CaptureStage(1, "normalize", "Emit normalized YAML for new or changed entries.", ...),
    CaptureStage(2, "summarize", "Generate daily summaries for affected dates.", ...),
    CaptureStage(3, "extract_facts", "Derive micro-facts and claim proposals for affected dates.", ...),
    CaptureStage(4, "profile_update", "Generate profile suggestions and optionally apply them.", ...),
    CaptureStage(5, "characterize_review", "Characterize entries and review new batches (auto-applied in capture).", ...),
    CaptureStage(6, "index_refresh", "Refresh the retrieval index for new evidence.", ...),
    CaptureStage(7, "persona_refresh", "Rebuild persona core when profile data changes.", ...),
    CaptureStage(8, "pack", "Emit context packs when requested (depends on --pack option).", ...),
]
```

Each stage hands off to deterministic helpers under `services/capture/stages/`. Outputs are persisted as `Artifact[T]` envelopes through `aijournal/io/artifacts.py`, guaranteeing reproducibility and schema validation.

### 2.3 CLI Layering
`src/aijournal/cli.py` holds thin Typer commands that delegate to `commands/` modules. Examples:
- `capture` → `services.capture.run_capture`
- `ops pipeline summarize` → pipeline wrappers in `commands/summarize.py`
- `ops persona build` → `commands/persona.py`
- `ops index rebuild` → `commands/index.py`
- `advise`, `chat`, `serve chat` map to orchestrators in `commands/advise.py`, `commands/chat.py`, `commands/chatd.py`

### 2.4 Data Flow Summary
1. **Ingestion:** `capture` stage 0 writes Markdown (`data/journal/YYYY/MM/DD/*.md`), raw snapshots, and manifest entries.
2. **Normalization:** Stage 1 emits `data/normalized/YYYY-MM-DD/*.yaml` via `pipelines/normalization.py`.
3. **Derivations:** Stages 2-5 produce summaries, microfacts, profile proposals, and characterization batches (`derived/summaries/`, `derived/microfacts/`, `derived/profile_proposals/`, `derived/pending/profile_updates/`).
4. **Retrieval:** Stage 6 updates `derived/index/` (SQLite FTS, Annoy vectors, chunk manifests) via `services/retriever.py` and `pipelines/index.py`.
5. **Persona & Packs:** Stage 7 rebuilds `derived/persona/persona_core.yaml`; stage 8 optionally emits context packs (`derived/packs/`).

## 3. Domain & Schema Governance
### 3.1 Claim Atoms
`src/aijournal/domain/claims.py` defines the authoritative claim structure:

```python
class ClaimAtom(StrictModel):
    id: str
    type: Literal["preference", "value", "goal", "boundary", "trait", "habit", "aversion", "skill"]
    subject: str
    predicate: str
    value: str
    statement: str
    scope: Scope = Field(default_factory=Scope)
    strength: float = Field(default=0.5, ge=0.0, le=1.0)
    status: Literal["accepted", "tentative", "rejected"] = "tentative"
    method: Literal["self_report", "inferred", "behavioral"]
    user_verified: bool = False
    review_after_days: int = 120
    provenance: Provenance
```

- `Scope` captures domain/context/conditions; `Provenance` holds redacted `SourceRef` instances.
- `ClaimConsolidator` (`src/aijournal/services/consolidator.py`) merges observations using logarithmic weighting and exponential decay while preserving provenance redaction.

### 3.2 Evidence Discipline
`src/aijournal/domain/evidence.py` keeps `Span.text` optional but `ClaimAtom._ensure_redacted` (validator) blocks persisted spans containing text. Audit command `aijournal ops audit provenance --fix` repairs lingering text.

### 3.3 Microfacts & Summaries
`src/aijournal/domain/facts.py` structures microfacts and daily summaries:

```python
class MicroFactsFile(StrictModel):
    facts: list[MicroFact] = Field(default_factory=list)
    claim_proposals: list[ClaimProposal] = Field(default_factory=list)
    preview: ProfileUpdatePreview | None = None
```

Current `prompts/extract_facts.md` only requests the `facts` array, so claim proposals and preview are never emitted—see §5.3.2 for remediation.

### 3.4 Artifact Validation
- All domain models inherit from `StrictModel` (`aijournal/common/base.py`), forbidding unknown keys.
- `scripts/check_schemas.py` snapshots JSON Schemas under `schemas/core/` and runs in the bundled pre-push hook.
- `tests/test_ollama_services.py` confirms `run_ollama_agent` logs structured failures and translates provider errors into `LLMResponseError`.
- Historical per-payload meta classes (`SummaryMeta`, `PersonaCoreMeta`) have been removed from `src/aijournal/domain`. All feature-specific metadata must now live inside `ArtifactMeta.notes`. Audit remaining docs (for example `refactor3.md`) and schema snapshots so they no longer reference the deleted meta structures.

## 4. Services & Pipelines
### 4.1 Ollama Runner (`src/aijournal/services/ollama.py`)
- `OllamaConfig` resolves precedence (explicit args → env vars → config → defaults).
- `_JSON_SYSTEM_PROMPT` enforces JSON-only responses.
- `run_ollama_agent` returns `LLMResult[payload]`. On parse failure it raises `LLMResponseError` and writes diagnostics under `derived/logs/structured_failures/<agent>/` (asserted in tests).

### 4.2 Capture Service
Stage handlers live in `services/capture/stages/` and return `OperationResult`/`StageResult` objects with `changed`, `artifacts`, `details`, and `warnings`, mirroring the workflow guidance in `docs/workflow.md`.

### 4.3 Retriever (`src/aijournal/services/retriever.py`)
- Builds SQLite FTS and Annoy indexes; scoring combines cosine similarity with a recency term (`score = 0.7 * cosine + 0.3 * recency`, `recency = 1 / (1 + 0.05 * days_since)`).
- CLI surfaces (`ops index rebuild`, `ops index search`) exercise these paths; `tests/test_retriever.py` asserts deterministic results against fixture workspaces.
- Retrieval helpers now return `StrictModel` instances (`RetrievalFilters`, `RetrievalMeta`, `RetrievalResult`, `RetrievedChunk`). Keep all structs crossing CLI/API boundaries as `StrictModel`s; reserve plain dataclasses for internal helpers only.

### 4.4 Persona Builder (`src/aijournal/pipelines/persona.py`)
- Ranks claim atoms via `effective_strength × impact_weight` using weights in `config.yaml`.
- Emits `derived/persona/persona_core.yaml`, recording source mtimes to detect staleness. `aijournal ops persona status` reports when rebuilds are required; packs refuse to run against stale persona cores.

## 5. Prompt Surfaces – Findings & Actions
External feedback (LLM without repository access) aligned with local inspection; the following issues are confirmed in the prompt files.

### 5.1 `prompts/profile_suggest.md`
**Observed in repository:**
- Requests full `ClaimAtom` (including `provenance`) even though the system generates IDs/provenance server-side.
- Evidence spans use `"type": "paragraph"`, inconsistent with other prompts (`"para"`).
- Facet `value` permits arbitrary JSON objects; `operation` allows `merge`.

**Actions:**
1. Restrict LLM output to `ClaimAtomInput` fields: `type`, `subject`, `predicate`, `value`, `statement`, `scope`, `strength`, `status`, `method`, `user_verified`, `review_after_days`.
2. Keep evidence at the proposal level (`"evidence": [{"entry_id": ..., "spans": [{"type": "para", "index": 0}]}]`).
3. Limit `value` to `string | list[string]`; drop `merge` option so downstream logic stays deterministic.
4. Standardize fallback to `{"claims": [], "facets": []}` and instruct the model not to add extra keys.
5. Update prompt text/examples to list the exact enum values (`type`, `status`, `method`) pulled from the shared enums file, then update `prompts/examples/profile_suggest.json` and relevant tests.

### 5.2 `prompts/characterize.md`
**Observed:** Example payload includes `claim.id`, `provenance`, `normalized_ids`, `evidence_hashes`, and facet values as arbitrary objects. Instructions already require the top-level keys but not the input shape.

**Actions:**
1. Switch schema language to `ClaimAtomInput`; remove `id`/`provenance` from the LLM response.
2. Require evidence spans to use `"type": "para"`; default to empty list only when evidence truly lacks spans.
3. Constrain facet `value` to `string | list[string]` and keep `operation` to `set|remove`.
4. Reinforce `rationale ≤ 25 words` and `interview_prompts ≤ 20 words`.
5. Update prompt text/examples to enumerate allowed values (`claim.type`, `status`, `method`) and sync `prompts/examples/characterize.json` plus tests.

### 5.3 `prompts/extract_facts.md`
**Observed:** Output skeleton only contains `"facts"` array despite `MicroFactsFile` expecting `claim_proposals` and `preview`.

**Actions:** Prefer to keep the LLM payload minimal (`{"facts": [...]}`) and let the pipeline wrap it into `Artifact[MicroFactsFile]` with `claim_proposals: []` and `preview: null`. If you opt to enforce the full envelope at the prompt layer, update the skeleton and fallback accordingly.

### 5.4 `prompts/advise.md`
**Observed:**
- Prompts the LLM to fabricate `"id": "adv_YYYY-MM-DD_xxxx"`.
- `style` block expects free-text `tone` / `depth` values.
- No caps on list lengths.

**Actions:**
1. Set `id` to `null` so the backend can generate consistent IDs.
2. Restrict `style` to enums (`tone: direct|coaching|warm|concise|null`, `reading_level: basic|intermediate|advanced|null`) and optional booleans (`include_risks`, `coaching_prompts`). Allow `style: null`.
3. Introduce caps: ≤3 recommendations, ≤5 steps, ≤3 risks/mitigations, ≤160 characters per list item.
4. Provide minimal fallback with empty arrays and `style: null`.

### 5.5 `prompts/summarize_day.md`
- Add ≤18 word cap per line and switch fallback to `{ "day": "$date", "bullets": [], "highlights": [], "todo_candidates": [] }` instead of error objects.

### 5.6 `prompts/interview.md`
- Clarify `probing.max_questions ≤ 3`, question length ≤20 words, and require `target` references to use either a profile facet path or `claim:<id>` so downstream tooling can resolve follow-ups cleanly.

## 6. Runner-Level Enhancements
### 6.1 Skeleton Injection (Implemented)
`services/ollama.run_ollama_agent` now derives a JSON skeleton from the declared response model and appends it to the prompt automatically. Every structured call—facts, characterization, advice, chat—receives the same skeleton-edit workflow without duplicating logic in individual commands. The helper draws from shared enums and nested `StrictModel`s so the skeleton mirrors the schema exactly.

### 6.2 Two-Step Repair Loop + Deterministic Coercions
The same runner performs a two-stage recovery: invalid JSON triggers a logged retry with explicit errors and the previous payload, and schema validation failures invoke a light coercion pass (e.g., wrapping scalars in lists, normalising nested structures). Coercions are captured alongside the validated payload, and further failures re-prompt with error summaries. Final errors raise `LLMResponseError` with the aggregated issues plus a failure log entry under `derived/logs/structured_failures/`.

### 6.3 Coercion Policy
| Category | Example | Policy |
| --- | --- | --- |
| Format normalization | Trim whitespace, lowercase enums, convert `"yes"/"no"` to bool, wrap scalar as list when list expected | Allowed with logging |
| Enum synonym mapping | `"brief" → "concise"`, `"straightforward" → "direct"` | Allowed with logging (future work) |
| Safe defaults | Missing optional field → `null`/`[]`; `id → null` | Allowed |
| Semantic shifts | Changing `claim.type`, inventing provenance timestamps | Prohibited (fail fast) |
| Structure invention | Parsing arbitrary dicts into typed objects | Prohibited (re-prompt) |

Coercions applied inside the runner are emitted through `LLMResult.coercions_applied` for downstream recording (e.g., into `ArtifactMeta.notes`).

### 6.4 Metrics
`LLMResult` now records `attempts`, `repair_attempts`, and `coercions_applied`. Successful calls append a telemetry row to `derived/logs/structured_metrics.jsonl`; `scripts/check_structured_metrics.py` enforces fleet-wide thresholds (repair rate ≤10%, average coercions ≤3) and is covered by dedicated tests.

### 6.5 Structured Logging & Command Skeleton (Implemented)
- **RunContext & StructuredLogger:** New shared utilities (`src/aijournal/common/context.py`, `common/logging.py`) create a per-command `RunContext` with a monotonic `run_id`, resolved config, fake/live flag, and a `StructuredLogger`. Logs append to `derived/logs/run_trace.jsonl` and can be mirrored to stdout with `--trace` (pretty) or `--verbose-json` (NDJSON) flags. Capture telemetry (`services/capture/__init__.py`) now reuses the same `StructuredLogger` to avoid duplicate writers.
- **Standardized command pipeline:** `run_command_pipeline` (`common/command_runner.py`) wraps the three-phase pattern—`prepare_inputs`, `invoke_pipeline`, `persist_output`—and automatically emits `command_start`, `start/end/error` span events, and `command_complete`. The helper also serializes Pydantic models safely (callables are rendered by name, `Path` objects become strings).
- **Pilot rollout:** High-complexity commands (`commands/advise.py`, `commands/summarize.py`, `commands/facts.py`) now expose `Options` models plus `prepare_inputs`/`invoke_pipeline`/`persist_output` helpers. CLI entrypoints use `_run_context(...)` to pass a typed context instead of mingling Typer options with orchestration logic. Capture stages keep calling the compatibility wrappers (`run_advise`, `run_summarize`, `run_facts`), which delegate into the new pipeline code.
- **Trace inspection:** A follow-up CLI helper (`aijournal ops logs tail`) is recommended so users can read the latest trace entries without opening the JSONL file manually.

## 7. Scientific Credibility & Validation Plan
The L1→L4 memory hierarchy aligns with contemporary personality science when treated as a measurement system.

| Concept | Relevance | Reference |
| --- | --- | --- |
| Language-driven trait inference | Journals provide measurable signals of enduring traits | Youyou et al., 2015 (PNAS) [1] |
| Self/Other Knowledge Asymmetry | Journals over-index on internal states; solicit external feedback | Vazire, 2010 (JPSP) [2] |
| Expressive writing & narrative identity | L4 background supports meaning-making and well-being | Pennebaker & Chung, 2007 [3] |
| Traits as density distributions | Aggregating L2 states into L1 strengths is theoretically sound | Fleeson, 2001 (JPSP) [4] |
| Implementation intentions | Advice should encode concrete "if-then" plans | Gollwitzer, 1999 [5] |
| COM-B behaviour change | Tag advice with capability/opportunity/motivation levers | Michie et al., 2011 [6] |
| Habit formation timelines | Median ≈66 days; informs decay/review cadence | Lally et al., 2010 [7] |
| McAdams’ New Big Five | Provides conceptual map for L1 (core) through L4 (narrative) | McAdams & Pals, 2006 [8] |

### 7.1 Measurement Checklist
1. **Convergent validity:** Administer monthly short BFI-2/IPIP + Schwartz values; correlate with grouped claim strengths (target `r ≥ .30`).
2. **Discriminant validity:** Keep cross-trait correlations |r| < .25.
3. **Test–retest reliability:** Compute ICCs for claim families over 4–6 weeks; expect ≥0.60 absent major life changes.
4. **State density integration:** Collect EMA prompts (1–3 items/day) and align L1 strengths with EMA means/variances.
5. **Predictive validity:** Track baseline vs. post-advice success (e.g., completed focus blocks for implementation intentions).
6. **Calibration:** Produce Brier scores & reliability curves for binary claims (e.g., “protects mornings for deep work”); adjust strength scaling when miscalibrated.
7. **Inter-rater reliability:** Run alternate prompt/model sweeps; compute Cohen’s κ / Jaccard on claim signatures.
8. **Kill criteria:** Pivot if convergent validity < .20, advice yields no improvement, or calibration remains poor after 8–12 weeks per user.

### 7.2 Instrumentation Hooks
- Store survey and EMA results as artifacts (`derived/evaluations/ema/YYYY-MM-DD.yaml`).
- Extend persona builder to incorporate observation counts and state variance.
- Add `strength_ci` once sufficient observations exist.

## 8. Implementation Roadmap
1. **Single-Meta Audit:** Confirm no residual `SummaryMeta`/`PersonaCoreMeta` usages, migrate lingering metadata into `ArtifactMeta.notes`, and refresh docs/schemas/examples to document the single-envelope invariant.
2. **Prompt Refactor & Advice Cut-Over:** Update templates per §5, remove `AdviceLLMResponse` in favor of direct `AdviceCard` payloads (with `id: null`, bounded `style`, capped lists), revise `prompts/examples/*`, and align regression tests.
3. **Schema Tightening:** Introduce shared `StrEnum` types (e.g., `domain/enums.py` housing `ClaimType`, `ClaimStatus`, `ClaimMethod`, `AdviceTone`, etc.), update domain models/prompts to reference the canonical vocabularies, and regenerate schemas with `uv run python scripts/check_schemas.py --bless` followed by a non-bless run.
4. **Runner Enhancements:** Implement skeleton injection, repair loop, and coercion logging in command modules; enrich `LLMResult` metadata.
5. **Telemetry Metrics:** Aggregate validation/coercion stats; alert on threshold breaches.
6. **Evaluation Toolkit:** Add `aijournal ops persona calibrate` for survey/EMA ingestion and `aijournal ops persona metrics` to compute correlations, ICCs, and calibration curves.
7. **Advice Mechanistic Tags:** Extend `AdviceCard` with `com_b_lever` and `if_then` fields; require each recommendation to cite ≥1 claim and ≥1 recent evidence ID.
8. **Trusted-Other Ingestion (Optional):** Provide `aijournal capture --trusted-other` for informant input, stored as separate evidence channels with independent decay.

### To-Do Checklist
- [x] Audit repo for legacy meta classes (`SummaryMeta`, `PersonaCoreMeta`); confirmed only historical docs mention them and no schema/code updates required.
- [x] Update all prompts and examples to the new ClaimAtomInput/enum/`para` span requirements and align the advice flow with strict `AdviceCard` payloads (bounded style enums, placeholder ID format).
- [x] Introduce shared `StrEnum` vocabularies in `domain/enums.py`, refactor models/prompts/tests to use them, and bless the resulting schema snapshots.
- [x] Add skeleton injection + two-step repair loop + coercion logging to structured runners, exposing metrics in `LLMResult` and CI (implemented in `services/ollama.run_ollama_agent`, now returning attempt/repair/coercion metadata consumed by CLI pipelines).
- [x] Build telemetry surfaces that aggregate validation/coercion counts and enforce thresholds in CI (`services/ollama.run_ollama_agent` now emits to `derived/logs/structured_metrics.jsonl`; thresholds enforced via `scripts/check_structured_metrics.py` and associated tests).
- [ ] Ship survey/EMA ingestion + reporting commands (`ops persona calibrate`, `ops persona metrics`) to track convergent/discriminant/test–retest/calibration stats and enforce kill criteria.
- [ ] Extend `AdviceCard` with COM-B / implementation-intention fields and ensure recommendations cite both claims and recent evidence.
- [ ] (Optional) Implement trusted-other ingestion with separate evidence channels and document usage.

## 9. Additional Observations
- **Pre-push Guard:** `.githooks/pre-push` runs schema checks, pytest, and pre-commit—ensure they pass after prompt/schema refactors.
- **Failure Archive:** Monitor `derived/logs/structured_failures/` after changes to confirm reduced error volume.
- **Persona Freshness:** `aijournal ops persona status` reports stale cores; consider auto-rebuilding when `profile/` timestamps change.
- **Run Logs:** Continue recording live-mode workflows in `/tmp/aijournal_live_run_*/run_log.md` for reproducibility.
- **Citation Contract:** Keep the two-layer citation model (IDs on the wire, resolved spans in transcripts) and call it out explicitly in contributor docs so no one reverts to raw strings.
- **Two-step Generation:** Where LLMs struggle, split large tasks into typed sub-steps (e.g., LLM emits facts only; server derives claim proposals and previews) to keep prompts simple and artifacts strict.

## 10. References
1. Youyou, W., Kosinski, M., & Stillwell, D. (2015). *Computer-based personality judgments are more accurate than those made by humans.* Proceedings of the National Academy of Sciences.
2. Vazire, S. (2010). *Who knows what about a person? The self–other knowledge asymmetry (SOKA) model.* Journal of Personality and Social Psychology.
3. Pennebaker, J. W., & Chung, C. K. (2007). *Expressive writing, emotional upheavals, and health.* In H. S. Friedman (Ed.), *Oxford Handbook of Health Psychology.*
4. Fleeson, W. (2001). *Toward a structure- and process-integrated view of personality: Traits as density distributions of states.* Journal of Personality and Social Psychology.
5. Gollwitzer, P. M. (1999). *Implementation intentions.* Advances in Experimental Social Psychology.
6. Michie, S., van Stralen, M. M., & West, R. (2011). *The behaviour change wheel.* Implementation Science.
7. Lally, P., Van Jaarsveld, C. H., Potts, H. W., & Wardle, J. (2010). *How are habits formed: Modelling habit formation in the real world.* European Journal of Social Psychology.
8. McAdams, D. P., & Pals, J. L. (2006). *A new Big Five: Fundamental principles for an integrative science of personality.* American Psychologist.

---

**Next Steps Checklist**
1. Apply prompt updates and regenerate schema artifacts.
2. Implement runner repair loop with coercion logging.
3. Wire evaluation metrics into CI, enforcing kill criteria thresholds.
4. Document new command surfaces (`ops persona calibrate`, etc.) in `docs/workflow.md` once implemented.
5. Run `uv run pytest -q` and `pre-commit run --all-files` before committing code changes derived from this review.
