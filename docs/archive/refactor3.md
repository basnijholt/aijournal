# Refactor Plan v3 – Strict Data Model & Artifact Unification

> **This plan is adaptive.** If any step proves sub-optimal in practice, do **not** press ahead blindly. Halt at the nearest checkpoint, document the issue in `docs/refactor3_status.md#decision-log`, record (1) what was attempted, (2) why it failed, (3) the alternative chosen, and (4) migration/testing impact. Resume only after the entry is written. The **MANDATORY CHECKPOINT RULE** still applies.
>
> **MANDATORY CHECKPOINT RULE (DO NOT VIOLATE):** After **every** numbered sub-step: (1) finish the listed tasks, (2) run all required commands (always `uv run pytest`, plus any additional checks spelled out for that sub-step), (3) confirm a green result, and (4) commit only the files touched in that sub-step using a precise message. Never proceed to the next sub-step with uncommitted changes or failing tests.

This document is the authoritative runbook for the strict-schema consolidation of `aijournal`. Every automation or AI agent must begin here, follow the sequence exactly, and produce the specified commit cadence. No human intervention will occur between steps, so clarity and determinism are paramount.

---

## 0. Executive Summary

- **Mission:** Collapse duplicated “payload vs. derived” schemas, enforce strict structured output from the LLM, introduce canonical `Artifact[T]` envelopes, and keep capture/feedback privacy safeguards intact.
- **Why now:** The current landscape (see `scripts/data_model_report.py`) shows dozens of nearly identical Pydantic models and dataclasses. This hampers validation, complicates reasoning, and forces coercion layers. A strict, unified schema makes the system safer, easier to audit, and ready for autonomous agents.
- **Key Deliverables:**
  1. Part A – Pain points & duplication map (for context).
  2. Part B – Unified strict data model (Pydantic class outlines + rationale).
  3. Part C – Migration plan (phased, with adapters, prompts, tests, risks).
  4. “Prompt Draft” for downstream agents ensuring they operate with these assumptions.

This runbook expands every part of the prior proposal into actionable, testable sub-steps with explicit commands, acceptance criteria, and commit checkpoints.

---

## 1. Reference Materials (Read/Refresh Before Stage 0)

| Path | Purpose |
| ---- | ------- |
| `README.md` | Product overview, CLI workflow, personas. |
| `ARCHITECTURE.md` | Authoritative system design (pipelines, claims, persona, retrieval). |
| `docs/workflow.md` | Daily operator flow; command ordering. |
| `AGENTS.md` | Live-mode rehearsal details, success criteria. |
| `scripts/data_model_report.py` | Inventory script for Pydantic models/dataclasses. |
| `refactor3.md` *(this file)* | Runbook – **do not modify structure without alignment**. |

**Agents must confirm they have read/skimmed each document before executing Stage 0.**

---

## 2. Guiding Principles & Non-Negotiables

1. **Strict Structured Output:** LLM responses must already conform to the same Pydantic classes we persist. Missing required fields = hard schema failure (retry once via existing machinery, then abort).
2. **Privacy:** Claim provenance must never persist raw text excerpts; micro-facts may carry text during analysis but it must be stripped before persisting claims.
3. **Capture DTO Separation:** Keep `CaptureRequest` (public) distinct from `CaptureInput` (internal with `min_stage`/`max_stage`).
4. **Event Shapes:** Preserve rich preview events and lightweight feedback adjustments via a discriminated union.
5. **Artifact Envelopes:** Persist derived outputs inside `Artifact[T]` envelopes with `kind`, `meta`, and `data` fields—no parallel formats.
6. **Atomic Commits:** Each sub-step ends with a passing test suite and a dedicated commit.
7. **Governance Hooks:** Install a local `pre-push` hook running `uv run pytest -q` and `pre-commit run --all-files`; pushes fail on red.
8. **Decision Log:** Maintain a “Decision Log” table (Date / Step / Decision / Impact) in `docs/refactor3_status.md`. Every deviation, retry, or adjustment must be recorded before continuing.
9. **Hard Cutover:** Eliminate legacy schema toggles. Run the entire refactor with the strict models only (`read-new-write-new` semantics) and delete helpers that attempted to bridge formats.

---

## 3. Part A – Pain Points & Duplication (Context)

| Area | Duplication | Notes |
| ---- | ----------- | ----- |
| **Payload vs. Derived Models** | `MicroFact` vs. `ExtractedFactPayload`; `AdviceRecommendation` vs. `AdviceLLMRecommendation`; `ClaimProposal` vs. `ClaimProposalPayload`; `FacetProposal` vs. `FacetProposalPayload`; `DailySummary` vs. `DailySummaryResponse`; `ProfileSuggestions` vs. `ProfileSuggestionsResponse` / `SimpleProfileSuggestionsResponse`. | Same semantics expressed twice to distinguish “LLM produced” vs. “persisted”. Leads to conversion code and inconsistent validation. |
| **Sections & Entities** | `ingest_agent.IngestSection` vs. `models.authoritative.JournalSection`. | Only difference is presence of `para_index`. |
| **Evidence** | `ClaimSource`/`ClaimSourceSpan` vs. `FactEvidence`/`FactEvidenceSpan`. | Identical structure; fact span adds optional `text`. |
| **Change Events** | `ClaimPreviewEvent` (Pydantic) vs. `FeedbackAdjustment` (dataclass). | Both describe claim changes but with different payload carriers. |
| **Conflicts/Signatures** | `services.consolidator.ClaimConflict` (dataclass) vs. `models.derived.ClaimConflictPayload` (Pydantic). | Redundant representations. |
| **Chat Responses** | `ChatLLMResponse`, `ChatTurn`, `ChatCitation`. | Mixed dataclass/Pydantic; inconsistent layering. |
| **Chunks & Index Meta** | ~~`ChunkManifestChunk` vs. `RetrievedChunk`; duplicate meta blocks.~~ | Resolved via `ChunkBatch` + `ArtifactKind.INDEX_CHUNKS` (2025-10-30). |
| **Capture DTOs** | `CaptureRequest` vs. `CaptureInput`. | Intentionally similar but extra fields on the latter; we retain both. |
| **Meta Blocks** | Many derived artifacts repeat `(llm_model, prompt_path, prompt_hash, created_at)`. | Needs central `ArtifactMeta`. |
| **Package Layout** | Domain/payload split via class names rather than module organization. | Reorganize into `aijournal.domain`, `aijournal.artifacts`, `aijournal.api`, etc. |

---

## 4. Part B – Unified Data Model (Strict)

### 4.1 Common Primitives

```python
# aijournal/common/types.py
ISODateStr = str         # 'YYYY-MM-DD'
TimestampStr = str       # ISO8601 string

# aijournal/common/base.py
from pydantic import BaseModel, ConfigDict

class StrictModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        validate_assignment=True,
        arbitrary_types_allowed=False,
    )

# aijournal/common/meta.py
from typing import Generic, TypeVar
from enum import StrEnum

T = TypeVar("T")

class ArtifactMeta(StrictModel):
    created_at: TimestampStr
    model: str | None = None
    prompt_path: str | None = None
    prompt_hash: str | None = None
    char_per_token: float | None = None
    sources: dict[str, str] | None = None
    notes: dict[str, str] | None = None

class ArtifactKind(StrEnum):
    PERSONA_CORE = "persona.core"
    SUMMARY_DAILY = "summaries.daily"
    MICROFACTS_DAILY = "microfacts.daily"
    PROFILE_UPDATES = "profile.updates"
    FEEDBACK_BATCH = "feedback.batch"
    INDEX_META = "index.meta"
    INDEX_CHUNKS = "index.chunks"
    PACK_L1 = "pack.L1"
    PACK_L3 = "pack.L3"
    PACK_L4 = "pack.L4"
    CHAT_TRANSCRIPT = "chat.transcript"

class Artifact(Generic[T], StrictModel):
    kind: ArtifactKind
    meta: ArtifactMeta
    data: T

class LLMResult(Generic[T], StrictModel):
    model: str
    prompt_path: str
    prompt_hash: str | None = None
    created_at: TimestampStr
    payload: T
```

### 4.2 Journal & Sections

```python
# aijournal/domain/journal.py
class Section(StrictModel):
    heading: str
    level: int
    summary: str | None = None
    para_index: int | None = None

class NormalizedEntity(StrictModel):
    type: str
    value: str
    extra: dict[str, object] = {}

class NormalizedEntry(StrictModel):
    id: str
    created_at: TimestampStr
    source_path: str
    title: str
    tags: list[str]
    sections: list[Section] = []
    entities: list[NormalizedEntity] = []
    summary: str | None = None
    source_hash: str | None = None
    source_type: str | None = None
```

### 4.3 Evidence & Privacy

```python
# aijournal/domain/evidence.py
class Span(StrictModel):
    type: str
    index: int | None = None
    start: int | None = None
    end: int | None = None
    text: str | None = None  # allowed for micro-facts only

class SourceRef(StrictModel):
    entry_id: str
    spans: list[Span] = []
```

A helper `redact_source_text` will strip `span.text` during claim persistence and the migration audit command will clean any persisted files that still contain text spans.

### 4.4 Claims & Provenance

```python
# aijournal/domain/claims.py
class Scope(StrictModel):
    domain: str | None = None
    context: list[str] = []
    conditions: list[str] = []

class Provenance(StrictModel):
    sources: list[SourceRef] = []
    first_seen: ISODateStr | None = None
    last_updated: ISODateStr
    observation_count: int = 0

class ClaimAtom(StrictModel):
    id: str
    type: Literal['preference','value','goal','boundary','trait','habit','aversion','skill']
    subject: str
    predicate: str
    value: str
    statement: str
    scope: Scope
    strength: float
    status: Literal['accepted','tentative','rejected']
    method: Literal['self_report','inferred','behavioral']
    user_verified: bool
    review_after_days: int
    provenance: Provenance

    @field_validator('strength')
    @classmethod
    def _check_strength(cls, v):
        if not 0.0 <= v <= 1.0:
            raise ValueError("strength must be in [0,1]")
        return v

    @field_validator('provenance')
    @classmethod
    def _strip_text(cls, prov: Provenance) -> Provenance:
        for src in prov.sources:
            for span in src.spans:
                if span.text is not None:
                    raise ValueError("claim provenance spans must not carry raw text")
        return prov

class ClaimsFile(StrictModel):
    claims: list[ClaimAtom] = []
```

### 4.5 Claim/Fast Facet Inputs

```python
# aijournal/domain/changes.py
class ClaimAtomInput(StrictModel):
    type: Literal['preference','value','goal','boundary','trait','habit','aversion','skill']
    subject: str
    predicate: str
    value: str
    statement: str
    scope: Scope
    strength: float
    status: Literal['accepted','tentative','rejected']
    method: Literal['self_report','inferred','behavioral']
    user_verified: bool
    review_after_days: int

class ClaimProposal(StrictModel):
    claim: ClaimAtomInput
    normalized_ids: list[str] = []
    evidence: list[SourceRef] = []
    manifest_hashes: list[str] = []
    rationale: str | None = None

class FacetChange(StrictModel):
    path: str
    operation: Literal['set','remove','merge']
    value: object | None = None
    method: str | None = None
    confidence: float | None = None
    review_after_days: int | None = None
    user_verified: bool | None = None
    evidence: list[SourceRef] = []
    rationale: str | None = None

    @field_validator('value')
    @classmethod
    def _value_requirement(cls, value, info):
        op = info.data.get('operation')
        if op in {'set', 'merge'} and value is None:
            raise ValueError("value required for set/merge operations")
        return value

class ProfileUpdateProposals(StrictModel):
    claims: list[ClaimProposal] = []
    facets: list[FacetChange] = []
    interview_prompts: list[str] = []
```

### 4.6 Events & Batches

```python
# aijournal/domain/events.py
class ClaimPreviewEvent(StrictModel):
    kind: Literal['preview'] = 'preview'
    action: Literal['upsert','update','delete','conflict','strength_delta']
    claim_id: str
    delta_strength: float | None = None
    statement: str | None = None
    value: str | None = None
    strength: float | None = None
    signature: dict | None = None
    conflict: dict | None = None
    related_claim_id: str | None = None
    related_action: str | None = None
    related_signature: dict | None = None

class FeedbackAdjustmentEvent(StrictModel):
    kind: Literal['feedback'] = 'feedback'
    claim_id: str
    old_strength: float
    new_strength: float
    delta: float

ClaimChangeEvent = Annotated[
    Union[ClaimPreviewEvent, FeedbackAdjustmentEvent],
    Field(discriminator='kind'),
]

# aijournal/domain/batches.py
class ProfileUpdatePreview(StrictModel):
    claim_events: list[ClaimPreviewEvent] = []
    interview_prompts: list[str] = []

class ProfileUpdateBatch(StrictModel):
    batch_id: str
    created_at: TimestampStr
    date: ISODateStr
    inputs: list[ProfileUpdateInput]
    proposals: ProfileUpdateProposals
    preview: ProfileUpdatePreview | None = None

class FeedbackBatch(StrictModel):
    batch_id: str
    created_at: TimestampStr
    events: list[FeedbackAdjustmentEvent]
```

### 4.7 Facts & Summaries

```python
# aijournal/domain/facts.py
from pydantic import Field

class MicroFact(StrictModel):
    id: str
    statement: str
    confidence: float
    evidence: SourceRef
    first_seen: ISODateStr | None = None
    last_seen: ISODateStr | None = None

class MicroFactsFile(StrictModel):
    facts: list[MicroFact] = []
    claim_proposals: list[ClaimProposal] = []
    preview: ProfileUpdatePreview | None = None

class DailySummary(StrictModel):
    day: ISODateStr
    bullets: list[str] = []
    highlights: list[str] = []
    todo_candidates: list[str] = []
```

### 4.8 Persona & Packs

```python
# aijournal/domain/persona.py
class PersonaCore(StrictModel):
    profile: dict[str, object]
    claims: list[ClaimAtom] = []
```

Persona artifacts attach metadata (token budgets, trim details, etc.) via `ArtifactMeta.notes["persona"]`; no separate `PersonaCoreMeta` schema persists.

### 4.9 Retrieval & Chunks

```python
# aijournal/domain/index.py
class Chunk(StrictModel):
    chunk_id: str
    normalized_id: str
    chunk_index: int
    text: str
    date: ISODateStr
    tags: list[str] = []
    source_type: str | None = None
    source_path: str
    tokens: int
    source_hash: str | None = None
    manifest_hash: str | None = None

class RetrievedChunk(Chunk):
    score: float

class IndexMeta(StrictModel):
    embedding_model: str | None = None
    vector_dimension: int | None = None
    chunk_count: int | None = None
    entry_count: int | None = None
    mode: str | None = None
    fake_mode: bool | None = None
    annoy_trees: int | None = None
    search_k_factor: float | None = None
    char_per_token: float | None = None
    since: ISODateStr | None = None
    limit: int | None = None
    touched_dates: list[ISODateStr] = []
    updated_at: TimestampStr | None = None
```

### 4.10 Chat & Advice

```python
from pydantic import Field

# aijournal/api/chat.py
class ChatCitation(StrictModel):
    chunk_id: str
    code: str
    normalized_id: str
    chunk_index: int
    source_path: str
    date: ISODateStr
    tags: list[str] = []
    score: float

class ChatResponse(StrictModel):
    answer: str = Field(..., max_length=4000)
    citations: list[str] = Field(default_factory=list)
    clarifying_question: str | None = None
    telemetry: dict[str, object] = Field(default_factory=dict)
    timestamp: TimestampStr | None = None

class ChatRequest(StrictModel):
    question: str = Field(min_length=1)
    top: int | None = Field(default=None, ge=1)
    tags: list[str] | None = None
    source: list[str] | None = None
    date_from: ISODateStr | None = None
    date_to: ISODateStr | None = None
    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.\-]+$")
    save: bool = True
    feedback: Literal['up','down'] | None = None

# aijournal/domain/advice.py
class AdviceReference(StrictModel):
    facets: list[str] = []
    claims: list[str] = []

class AdviceRecommendation(StrictModel):
    title: str
    why_this_fits_you: AdviceReference
    steps: list[str] = []
    risks: list[str] = []
    mitigations: list[str] = []

class AdviceCard(StrictModel):
    id: str
    query: str
    assumptions: list[str] = []
    recommendations: list[AdviceRecommendation] = []
    tradeoffs: list[str] = []
    next_actions: list[str] = []
    confidence: float | None = None
    alignment: AdviceReference
    style: dict[str, object] = {}
```

### 4.11 Capture DTOs

```python
# aijournal/api/capture.py
class CaptureRequest(StrictModel):
    source: Literal['stdin','editor','file','dir']
    text: str | None = None
    paths: list[str] = []
    source_type: Literal['journal','notes','blog']
    date: ISODateStr | None = None
    title: str | None = None
    slug: str | None = None
    tags: list[str] = []
    projects: list[str] = []
    mood: str | None = None
    apply_profile: Literal['auto','review'] = 'auto'
    rebuild: Literal['auto','always','skip'] = 'auto'
    pack: Literal['L1','L3','L4'] | None = None
    retries: int = 1
    progress: bool = False
    dry_run: bool = False
    snapshot: bool = True

class CaptureInput(CaptureRequest):
    min_stage: int = 0
    max_stage: int = 8
```

---

## 5. Part C – Migration Plan (Phased)

1. Introduce strict base classes, artifact primitives, schema governance (enum + JSON schemas) without behavioural change.
2. Collapse duplicated models (sections, evidence, proposals, facts, summaries) into strict domain modules.
3. Update prompts & structured-output validation to enforce strict schemas (with logged failures & example fixtures).
4. Unify change events and feedback handling.
5. Align chunk/index/chat/advice surfaces with the new domain structure; prove retriever parity.
6. Preserve capture DTO boundaries.
7. Adopt artifact envelopes with an immediate cut-over, remove leftover legacy files, add provenance scanner, and regenerate documentation/examples.
8. Run end-to-end rehearsal under new schema, log decisions, publish migration notes, and prepare release guidance.

---

## 6. Stage-by-Stage Execution Checklist

> **Reminder:** after each sub-step, run required commands → verify success → commit immediately.

### Stage 0 – Baseline & Audit

**0.1 Baseline Verification**
- Commands: `git status`, `uv run pytest`.
- Acceptance: clean tree, passing tests.
- Commit: *none* (baseline only).

**0.2 Inventory Snapshot**
- Commands:
  - `mkdir -p reports`
  - `uv run python scripts/data_model_report.py > reports/data_model_out.txt`
  - If keeping report tracked: add to repo; otherwise, ensure `.gitignore` covers it.
- Tests: `uv run pytest`.
- Commit message: `refactor3: baseline data-model inventory`.

### Stage 1 – Strict Base, Artifact Infrastructure & Schema Freeze

**1.1 StrictModel Introduction**
- Create `aijournal/common/base.py` with `StrictModel`.
- Update existing base models (e.g., `src/aijournal/models/base.py`) to re-export or subclass `StrictModel`.
- Touch only minimal files to compile.
- Tests: `uv run pytest`.
- Commit: `refactor3: introduce strict base model`.

**1.2 ArtifactMeta & Helpers**
- Add `aijournal/common/meta.py` (`ArtifactMeta`, `ArtifactKind`, `Artifact[T]`, `LLMResult[T]`).
- Implement `aijournal/io/artifacts.py` with helper functions: `save_artifact` and `load_artifact` (deterministic serialization using sorted keys, UTF-8, newline at EOF).
- Tests: `uv run pytest`; `pre-commit run --all-files`.
- Commit: `refactor3: add artifact envelope primitives`.

**1.3 Schema Snapshot & Governance Hooks**
- Generate JSON Schema files for every strict model under `schemas/core/<model>.json` using `model_json_schema()`; commit them.
- Add CI/local script (`scripts/check_schemas.py`) comparing regenerated schemas vs. committed versions; fail unless `SCHEMAS_BLESS=1` is set.
- Audit code for raw `kind` strings; replace with `ArtifactKind` values.
- Create `.githooks/pre-push` running `uv run pytest -q` and `pre-commit run --all-files`; document activation in `CONTRIBUTING.md`.
- Add a “Decision Log” table (Date / Step / Decision / Impact) to `docs/refactor3_status.md`.
- Tests: `uv run pytest`; run schema checker to confirm zero diff.
- Commit: `refactor3: freeze strict schemas and add governance hooks`.

### Stage 2 – Sections & Evidence

**2.1 Unified Section Model**
- Introduce `aijournal/domain/journal.py` with `Section`, `NormalizedEntry`, `NormalizedEntity`.
- Replace `IngestSection` / `JournalSection` references in ingest agent, normalization pipeline, tests.
- Regenerate fixtures if serialization changes.
- Tests: `uv run pytest`.
- Commit: `refactor3: unify journal section schema`.

**2.2 Evidence Consolidation & Privacy Enforcement**
- Create `aijournal/domain/evidence.py` with `Span`, `SourceRef`.
- Replace `ClaimSourceSpan`, `FactEvidenceSpan`, `ClaimSource`, `FactEvidence` everywhere.
- Implement `redact_source_text(SourceRef)` and ensure every path that writes claim provenance calls it.
- Scaffold CLI command `aijournal ops audit provenance` (logic implemented in Stage 7.3).
- Tests: `uv run pytest`; add unit test verifying attempts to persist spans with text raise.
- Commit: `refactor3: standardize evidence spans and enforce privacy`.

### Stage 3 – Strict Proposals, Facts, Summaries & Prompt Fixtures

**3.1 Remove Sketch/Payload Models — DONE**
- Domain claim/facet changes now flow through `aijournal/domain/changes.py`; pipelines emit `ProfileUpdateProposals` and convert to `ClaimAtom` without legacy shims.
- 197-test green run after enforcement; governance hooks verify pre-commit + schema hygiene.

**3.2 Strict Facts & Summaries — DONE**
- `aijournal/domain/facts.py` backs facts & summaries end-to-end; CLI and pipelines persist unified envelopes.
- Schema snapshots and fixtures refreshed; `uv run pytest` (197 tests) green.

**3.3 Prompt Updates & Structured Output Fixtures — DONE**
- Prompts embed JSON exemplars under `prompts/examples/`; unit tests guard structured output drift.
- Redaction + logging pathways confirmed; pre-commit clean.

**3.4 Structured-Output Runner Enhancements — DONE**
- `run_ollama_agent` returns `LLMResult[T]`, handles retries, and logs invalid payloads to governed paths.
- Chat/profile pipelines consume strict responses; CI hook + schema checker green.

### Stage 4 – Persona & Interview Domainization

**4.1 Persona Domain Module — DONE**
- Introduced `aijournal/domain/persona.py` with strict `PersonaCore*` and `Interview*` models plus forward-ref rebuild helpers; exports wired through `aijournal/domain/__init__.py`.

**4.2 Derived Aliases & Rebuild — DONE**
- `models/derived.py` now re-exports domain persona/interview models and drops duplicate definitions; schema snapshots refreshed for the new locations.

**4.3 Capture & CLI Wiring — DONE**
- CLI, persona commands, schema validators, and chat services import the domain persona models directly, ensuring a single source of truth.

**4.4 Schema Blessing & Docs — DONE**
- Regenerated persona/interview schemas (`schemas/core/aijournal.domain.persona.*`), updated prompts/examples, and documented the strict-schema flow.

### Stage 5 – Claim Events & Feedback

**5.1 Discriminated Union for Events — DONE**
- Introduced `aijournal/domain/events.py` with strict `ClaimSignaturePayload`, `ClaimConflictPayload`, `ClaimPreviewEvent`, and `FeedbackAdjustmentEvent` (discriminated on `kind`), updating CLI/pipelines to instantiate the domain models directly.
- `ProfileUpdatePreview` now stores the domain events, and schema snapshots were regenerated.

**5.2 Feedback Batches Formalization — DONE**
- Added the strict `FeedbackBatch` envelope and rewired chat feedback persistence/`ops feedback apply` to read/write the new schema.
- CLI/chat tests and fixtures updated to validate the structured events; schema snapshots refreshed.

### Stage 6 – Retrieval, Chat, Advice

**6.1 Chunk & Index Unification + Retriever Parity**
- Create `aijournal/domain/index.py` with `Chunk`, `RetrievedChunk`, `IndexMeta`.
- Update indexing pipeline, retriever service, and tests.
- Add mini-workspace fixture (`tests/fixtures/miniwk/`) and parity test to assert search results match pre-refactor IDs within tolerance.
- Tests: `uv run pytest`; run `AIJOURNAL_FAKE_OLLAMA=1 uv run aijournal ops index rebuild` if fixtures exist.
- Commit: `refactor3: unify chunk and index schema`.

**6.2 Chat & Advice DTOs — DONE**
- Replace `ChatLLMResponse`/`ChatTurn` with strict `ChatResponse`/`ChatCitation`.
- Collapse advice twins into `AdviceRecommendation`.
- Update CLI, services, tests, transcripts.
- Tests: `uv run pytest`; optional chat CLI smoke tests in fake mode.
- Commit: `refactor3: streamline chat and advice responses`.

### Stage 7 – Capture Separation Reinforced

**7.1 DTO Relocation & Validation**
- Define `CaptureRequest`/`CaptureInput` in `aijournal/api/capture.py`.
- Ensure CLI exposes only `CaptureRequest` fields.
- Add unit test verifying CLI schema lacks stage fields.
- Tests: `uv run pytest`.
- Commit: `refactor3: formalize capture request/input split`.

### Stage 8 – Artifact Adoption & Provenance Scanner

**8.1 Wrap Derived Artifacts in Artifact[T] — DONE**
- Convert persisted YAML/JSON (summaries, microfacts, persona core, profile updates, feedback batches, index meta/chunks, packs, chat transcripts) to `Artifact[T]` envelopes.
- Use deterministic serialization helper for JSON/YAML dumps.
- Tests: `uv run pytest`; add test ensuring each artifact has a valid `ArtifactKind` and stable formatting.
- Commit: `refactor3: adopt artifact envelopes for derived data`.

**8.2 Legacy Payload Removal**
- Rip out any legacy data readers/writers and delete the `AIJOURNAL_SCHEMA_MODE` flag.
- Drop redundant models, CLI options, and fixtures that referenced v1 layouts.
- Remove stale `derived/*` examples that predate artifacts; regenerate only the strict envelopes.
- Tests: `uv run pytest`.
- Commit: `refactor3: remove legacy schema paths`.

**8.3 Provenance Audit Command — DONE**
- Implement `aijournal ops audit provenance [--fix]` scanning `profile/claims.yaml` and derived artifacts for spans containing text.
- `--fix` mode applies `redact_source_text`; default mode reports offenders and exits non-zero.
- Tests: `uv run pytest`; add CLI test with deliberate offender.
- Commit: `refactor3: add provenance audit tooling`.

**8.4 Import Codemod — N/A**
- Obsolete after the hard cutover; the repository no longer carries legacy import paths that require automated rewriting.
- No action required.

### Stage 9 – Documentation & Examples

**9.1 Documentation Updates — DONE**
- Update `README.md`, `ARCHITECTURE.md`, `docs/workflow.md`, `agents.md` to describe strict schemas, artifact envelopes, privacy enforcement, event unions, and governance hooks with a hard cutover stance (no compatibility knobs).
- Tests: `uv run pytest`; `pre-commit run --all-files` (Markdown lint).
- Commit: `refactor3: document strict schema architecture`.

**9.2 Example Artifact Regeneration — DONE**
- Regenerate sample packs, persona core, index manifests, microfacts, feedback, and chat examples reflecting new format (fake mode allowed).
- Tests: `uv run pytest`.
- Commit: `refactor3: refresh strict schema examples`.

### Stage 10 – Validation & Release Prep

**10.1 End-to-End Rehearsal — DONE**
- Fake mode acceptable:
  - `export AIJOURNAL_FAKE_OLLAMA=1`
  - `uv run aijournal init --path /tmp/aijournal_refactor3`
  - Capture fixture entries; run `status`, `chat`, `advise`, `export pack`, `ops feedback apply`.
- Verify every artifact under `derived/` includes an `ArtifactKind` and no persisted provenance spans contain text.
- Tests: `uv run pytest`; optionally `pre-commit run --all-files`.
- Commit: `refactor3: verify end-to-end strict workflow`.

**10.2 Completion Log & Changelog — DONE**
- Update `docs/refactor3_status.md` summarizing executed steps, test commands, decision log entries.
- Update `CHANGELOG.md` with the strict schema cut-over, migration notes, codemod instructions, provenance audit command, and the removal of schema-mode toggles.
- Tests: `uv run pytest`.
- Commit: `refactor3: finalize strict schema release notes`.

**10.3 Release Checklist (Optional)**
- Draft instructions for tagging, packaging, communication (no tag push).
- Tests: `uv run pytest` to confirm no drift.
- Commit (if files added): `refactor3: prepare release checklist`.

---

## 7. Prompt & Validation Contracts (Detailed)

### 7.1 Structured Output Settings

- All pipelines pass `response_model=<StrictModel subclass>` to the structured-output runner.
- On schema failure: log validation errors, store raw payload under `derived/logs/structured_failures/<command>/`, retry once, then raise.
- Runner returns `LLMResult[T]` containing model/prompt info.

### 7.2 Command Expectations

| Command | Required Output Schema | Notes |
| ------- | ---------------------- | ----- |
| `aijournal ops pipeline summarize` | `DailySummary` | Arrays may be empty but must be present. |
| `aijournal ops pipeline extract-facts` | `MicroFactsFile` | Each fact has `evidence.entry_id` and ≥1 span; spans may include `text`. |
| `aijournal ops profile suggest` | `ProfileUpdateProposals` | Each claim is full `ClaimAtomInput`; omit the proposal entirely if strength unknown. |
| `aijournal ops pipeline characterize` | `ProfileUpdateProposals` + interview prompts | Same strict schema. |
| `aijournal advise` | `AdviceCard` | All sections present; arrays may be empty. |
| `aijournal chat`/`serve chat` | `ChatResponse` | Includes citations and timestamp. |

### 7.3 Prompt Snippets & Fixtures

- Every prompt includes a JSON example consistent with the strict schema.
- Fixtures under `prompts/examples/` are validated in unit tests to prevent drift between docs and models.

---

## 8. Testing Matrix

| Stage/Sub-step | Required Commands | Purpose |
| -------------- | ----------------- | ------- |
| All | `uv run pytest` | Baseline regression suite. |
| 1.x | `pre-commit run --all-files` (where noted) | Formatting/linting compliance. |
| 2.2 | Unit test verifying provenance text removal + audit scanner. |
| 3.x | `AIJOURNAL_FAKE_OLLAMA=1` pipeline smoke tests (recommended). |
| 5.2 | CLI feedback apply in fake mode to ensure event union works. |
| 6.1 | `aijournal ops index rebuild` in fake mode + parity assertion. |
| 8.1 | Golden file diff review (ensure only envelope + deterministic formatting changes). |
| 8.2 | Verify grep for removed legacy modules/files is empty; ensure CLI refuses old schema flags. |
| 8.4 | Codemod unit test. |
| 10.1 | Full workflow rehearsal in temp workspace. |

Add property tests for provenance redaction and event JSON round-trips during relevant stages.

---

## 9. Risk Register & Mitigations

| Risk | Mitigation |
| ---- | ---------- |
| LLM fails to output strict schema initially | Enhanced prompts, structured-output retry, logged failures, schema fixtures. |
| Claim provenance accidentally stores text | Validators, `redact_source_text`, audit command, dedicated tests. |
| Artifact conversion corrupts existing data | Back up via git, run e2e rehearsal before deleting old files, deterministic serialization, audit command. |
| Search/index mismatch | Retriever parity test with mini workspace. |
| Schema drift after merge | JSON schema snapshots + CI diff, ArtifactKind enum. |
| Commit discipline lapses | Checkpoint rule + pre-push hook. |

---

## 10. Logging & Evidence Collection

- Store structured-output failures under `derived/logs/structured_failures/`.
- Update `docs/refactor3_status.md` after every stage with test commands executed, decisions taken, and outstanding follow-ups.
- Archive regenerated artifacts for PR review.

---

## 11. Commit Message Template

```
refactor3: <concise summary>

- Stage X.Y – <sub-step description>
- Commands: <list executed>
- Tests: uv run pytest [ + others ]
- Notes: <optional observations>
```

---

## 12. Final Exit Criteria

- All stages complete with passing tests and sequential commits.
- `git status` clean; no leftover artifacts or temporary files.
- `docs/refactor3_status.md` contains Decision Log entries and execution summary.
- `CHANGELOG.md` documents the strict schema cut-over, audit command, codemod, and hard removal of legacy formats.
- Prompts updated; schema snapshots in place with no legacy fallbacks.
- Optional release checklist prepared if required.

---

## 13. Appendix A – “Prompt Draft” for Delegated Agents

> **Role**: Senior software architect & data modeler for `aijournal`.
>
> **Inputs**: `README.md`, `ARCHITECTURE.md`, `docs/workflow.md`, `agents.md`, `refactor3.md`, `reports/data_model_out.txt`.
>
> **Objective**: Implement the strict data model, replacing payload/response twins with unified domain classes, adopting artifact envelopes, enforcing privacy, preserving capture/event semantics, and delivering the staged commits in `refactor3.md`.
>
> **Hard Requirements**:
> 1. Strict structured output only; missing required fields = failure. Runner returns `LLMResult[T]` and logs invalid payloads.
> 2. Capture DTO separation (`CaptureRequest` public, `CaptureInput` internal with stage controls).
> 3. Evidence privacy – no `span.text` persists; use `redact_source_text` and `aijournal ops audit provenance`.
> 4. Distinct preview vs. feedback events via discriminated union.
> 5. All pipeline outputs are `Artifact[T]` with `ArtifactKind`; deterministic serialization enforced by helper.
> 6. Tests must pass after each `refactor3` sub-step; commit immediately.
> 7. Document deviations in `docs/refactor3_status.md#decision-log` before proceeding.
>
> **Deliverables**: Completed code per checklist, updated documentation, migration utilities, codemod script, schema snapshots, changelog entry, decision log updates, and green test evidence.

---

## 14. Appendix B – Command Quick Reference

| Purpose | Command |
| ------- | ------- |
| Run tests | `uv run pytest` |
| Lint/format | `pre-commit run --all-files` |
| Inventory models | `uv run python scripts/data_model_report.py` |
| Schema snapshot check | `uv run python scripts/check_schemas.py` |
| Pipeline smoke (fake mode) | `AIJOURNAL_FAKE_OLLAMA=1 uv run aijournal ops pipeline summarize --date 2025-10-26` |
| Audit provenance | `uv run aijournal ops audit provenance --fix` |
| Codemod imports | `uv run python scripts/codemods/refactor3_imports.py --apply path/to/file.py` |

---

## 15. Outstanding TODOs (Post-Cutover Cleanup)

Back-compat is a non-goal. The next agent should treat the items below as mandatory follow-ups so the refactor vision actually lands. Each bullet spells out the concrete work, affected modules, and what tests/docs must change. Update the Decision Log once each is complete.

1. **Retire `ProfileSuggestions` twins**
   - Replace `ProfileSuggestions`, `ProfileSuggestionUpsert`, `ProfileSuggestionUpdate` with the strict domain flow built around `ProfileUpdateProposals` + `ClaimProposal`/`FacetChange`.
   - Rewrite `src/aijournal/commands/profile.py`, `src/aijournal/pipelines/profile.py` (if any), and all CLI helpers/tests (`tests/test_cli_profile_*`, `tests/test_profile_suggestions_bridge.py`) to consume `ProfileUpdateProposals` directly and persist `Artifact[ProfileUpdateProposals]` instead of the bespoke container.
   - Delete the legacy models from `src/aijournal/models/derived.py`, adjust fake data helpers, schema snapshots, docs, and examples accordingly.

2. ~~**Remove chunk manifest duplicates**~~ ✅ _Completed 2025-10-30_
   - Daily chunk exports now persist as `ArtifactKind.INDEX_CHUNKS` envelopes containing `ChunkBatch` payloads (day + `Chunk` list) with embedder metadata in `ArtifactMeta.notes`.
   - `ChunkManifest*` models were deleted; `write_chunk_manifests` now writes artifact envelopes and NumPy shards only. Tests gained coverage for the new structure (`tests/pipelines/test_index.py::test_write_chunk_manifests`).
   - Docs (`README.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, `docs/archive/PLAN-v0.3.md`) and schema snapshots were refreshed to describe the new artifact format.

3. ~~**Promote chat citations to typed objects**~~ ✅ _Completed 2025-10-30_
   - `ChatResponse.citations` now exposes `list[ChatCitationRef]`; chat orchestration resolves these refs into full `ChatCitation`s before rendering.
   - CLI/FastAPI streaming paths emit structured citation objects (with `code` + `marker`), and transcript/summary recorders consume the shared domain model.
   - Schema snapshots, model inventory, and regression suites (`tests/test_chatd.py`, `tests/test_cli_chat.py`) were refreshed to assert on the typed payload.

4. ~~**Align claim preview action vocabulary**~~ ✅ _Completed 2025-10-30_
   - `ClaimPreviewEvent.action` now uses the canonical enum (`upsert`, `update`, `delete`, `conflict`, `strength_delta`); consolidator outcomes were remapped accordingly.
   - CLI previews/renderers and profile pipelines report the new actions, augmenting conflict events with spawned-claim context.
   - Schema snapshots and tests (`tests/test_consolidator.py`, `tests/test_cli_characterize.py`, `tests/test_cli_facts.py`) were updated to reflect the vocabulary change.

5. ~~**Normalize timestamps as ISO strings**~~ ✅ _Completed 2025-10-30_
   - `IngestResult.created_at` now stores ISO8601 strings; fake ingest paths use `format_timestamp`, and downstream normalization no longer sees raw `datetime` objects.
   - Updated tests (`tests/pipelines/test_normalization.py`, `tests/test_ingest_agent.py`) and schema snapshots to embrace the string representation.

6. ~~**Remove double-meta payloads**~~ ✅ _Completed 2025-10-30_
   - Persona artifacts now persist `Artifact[PersonaCore]`; persona metadata is serialized via `ArtifactMeta` (`char_per_token`, `notes`, `sources`).
   - Interview sets dropped their embedded `SummaryMeta`. Docs/examples and schema snapshots reflect the single-envelope approach.

7. ~~**Audit mutable defaults in domain models**~~ ✅ _Completed 2025-10-30_
   - Reviewed all strict domain models to confirm list/dict fields rely on `Field(default_factory=...)`; added regression coverage via the existing Pydantic schema snapshot tooling.

---

## 16. Absolute Final Reminder

At every sub-step boundary:

1. **Finish assigned tasks exactly as written.**
2. **Run required commands (always `uv run pytest`, plus extras).**
3. **Confirm success (no tolerated failures).**
4. **Commit immediately with an informative message.**

Any agent that cannot satisfy these conditions must halt and report back rather than improvising. This discipline keeps the refactor auditable and safe for automation.

---

## 17. Detailed Follow-Up Work Packages (Post-Review Commitments)

> **Context:** An external reviewer examined `README.md`, `ARCHITECTURE.md`, `docs/workflow.md`, `AGENTS.md`, `refactor3.md`, `docs/refactor3_status.md`, and `reports/data_model_out.txt` without direct code access. Their actionable feedback revealed four concrete gaps between the documented plan and the current codebase. This section decomposes those gaps into executable work packages so future agents can complete the cleanup without re-discovery or guesswork. Treat each package as a mini-stage: run green tests, regenerate schemas, and commit before proceeding.

### 17.1 Retire `AdviceLLMResponse` (Strict Advice DTO Cutover)

- **Problem:** The legacy response model `AdviceLLMResponse` still mediates between the LLM and downstream services (`src/aijournal/models/responses.py:11`, consumed by `commands/advise.py`, `pipelines/advise.py`, and unit tests). The refactor intent is for the LLM to emit the strict `AdviceCard` domain model directly.
- **Scope of change (fine-grained):**
  1. **Model deletion:** Remove `AdviceLLMResponse` from `src/aijournal/models/responses.py`; delete any import aliases and prune `__all__` exports.
  2. **Command rewrite:** In `src/aijournal/commands/advise.py`:
     - Update imports to pull `AdviceCard` and `LLMResult` from `aijournal.domain.advice` / `aijournal.common.meta`.
     - Change `_advice_payload` to invoke `_invoke_structured_llm(..., response_model=AdviceCard)` and accept the returned `AdviceCard` directly.
     - Ensure the retry message still enumerates required keys—rewrite it to match the domain field names exactly.
     - Confirm `Artifact[AdviceCard]` serialization remains unchanged.
  3. **Pipeline update:** In `src/aijournal/pipelines/advise.py`, change `AdviceRequest` type alias to `Callable[[], AdviceCard]`; the pipeline should no longer call `AdviceCard.model_validate(...)` because the LLM already returns a validated instance.
  4. **Fake path alignment:** Verify `fake_advise` already emits `AdviceCard`. If it returns another structure, update it to comply.
  5. **Tests/fixtures:** Update `tests/pipelines/test_advise.py`, `tests/prompts/test_prompt_examples.py`, and any fixture JSON/YAML that referenced `AdviceLLMResponse`. Adjust assertions to reflect the strict domain model (e.g., `advice_card.recommendations[0].why_this_fits_you.claims`).
  6. **Schema pruning:** Delete `schemas/core/aijournal.models.responses.AdviceLLMResponse.json`. Regenerate schemas (`uv run python scripts/check_schemas.py --bless`) so the AdviceCard schema appears under `aijournal.domain.advice.*` only.
  7. **Documentation:** Append a Decision Log entry and update `CHANGELOG.md` if it still references the legacy DTO.
- **Acceptance criteria:**
  - `git grep "AdviceLLMResponse"` returns only historical references (e.g., changelog) or zero results.
  - `uv run pytest -q` passes.
  - Prompt examples under `prompts/examples/advise.json` validate against `AdviceCard` via `tests/prompts/test_prompt_examples.py`.
  - Decision log entry added in `docs/refactor3_status.md` (Date, Step “Post-Review Cleanup”, Decision summary, Impact).

### 17.2 Collapse Per-Feature Meta (`SummaryMeta`, `PersonaCoreMeta`)

- **Problem:** Despite Stage 8’s goal, `SummaryMeta` (`src/aijournal/domain/facts.py:17`) and `PersonaCoreMeta` (`src/aijournal/domain/persona.py:13`) persist as separate payload structs used by multiple commands/tests. Artifact envelopes therefore carry duplicate metadata (payload meta + `ArtifactMeta`).
- **Scope of change (fine-grained):**
  1. **Delete model classes:** Remove `SummaryMeta` and `PersonaCoreMeta` definitions from their domain modules. Update any `__all__` or re-export blocks.
  2. **Artifact writers:** Replace calls such as `_artifact_meta_from_summary_meta(meta)` with direct instantiation of `ArtifactMeta(...)`. Record any auxiliary fields (e.g., `prompt_path`, `prompt_hash`, `token_budget`, `planned_tokens`, `char_per_token`, `generated_at`) inside `ArtifactMeta.notes` using namespaced keys (`{"summary": {...}}`, `{"persona": {...}}`).
  3. **Artifact readers:** Wherever code previously expected `artifact.data.meta`, rewrite it to read from `artifact.meta.notes`. Add helper functions (e.g., `_persona_notes(artifact.meta) -> PersonaNotes`) if needed to keep callsites tidy.
  4. **Serialization helpers:** Remove helper functions that manufactured `SummaryMeta` / `PersonaCoreMeta`. Introduce `build_summary_notes(...)` / `build_persona_notes(...)` returning dicts stored in `ArtifactMeta.notes` when helpful.
  5. **Tests & fixtures:** Update integration tests and snapshots to assert on `artifact.meta.notes`. Notable files: `tests/services/test_capture.py` (fake artifacts), `tests/test_cli_pack.py`, `tests/test_models_io.py`, persona builder tests, pack golden files under `docs/examples/`.
  6. **Schema snapshots:** Delete `schemas/core/aijournal.domain.facts.SummaryMeta.json` and `schemas/core/aijournal.domain.persona.PersonaCoreMeta.json`. Regenerate schemas and review diffs for `ArtifactMeta.notes` content.
  7. **Docs:** Update `ARCHITECTURE.md` (artifact section) and `docs/refactor3_status.md` Decision Log to cite the single-meta invariant. If `README.md` or `docs/workflow.md` mention `SummaryMeta`, revise them.
- **Acceptance criteria:**
  - `rg "SummaryMeta"` and `rg "PersonaCoreMeta"` only match changelog/history after removal.
  - Artifact YAML for summaries, microfacts, persona, and profile updates show meta fields exclusively inside the envelope’s `meta` block (no nested `meta` under `data`).
  - Schema snapshots updated: drop `aijournal.domain.facts.SummaryMeta.json` and `aijournal.domain.persona.PersonaCoreMeta.json`.
  - Full test suite green; document the change in `docs/refactor3_status.md` and adjust `ARCHITECTURE.md` to state that `ArtifactMeta` is the sole meta carrier.

### 17.3 Normalize Cross-Surface Types (Replace IO Dataclasses with Strict Models)

- **Problem:** Several dataclasses that pre-date Refactor3 still travel across CLI/service boundaries:
  - Consolidator events: `ClaimSignature`, `ClaimConflict`, `ClaimMergeOutcome` in `src/aijournal/services/consolidator.py`, bubbled up through CLI rendering and profile commands.
  - Feedback adjustments: same dataclasses exposed when applying profile updates.
  - Retrieval results: `RetrievalFilters`, `RetrievalMeta`, `RetrievalResult` in `src/aijournal/services/retriever.py` are returned to consumers.
- **Plan (fine-grained):**
  1. **Consolidator domain module:** Add `aijournal/domain/consolidator.py` (or extend `domain/events.py`) with `ClaimSignature`, `ClaimConflict`, and `MergeOutcome` as `StrictModel` classes. Ensure `MergeOutcome.action` uses the canonical enum (`PreviewAction`).
  2. **Service refactor:** Rewrite `ClaimConsolidator.upsert()` in `src/aijournal/services/consolidator.py` to build these strict models directly. Return `MergeOutcome` objects and embed conflicts inline (`MergeOutcome.conflict: ClaimConflict | None`).
  3. **CLI/profile consumers:** Update `src/aijournal/cli.py` and `src/aijournal/commands/profile.py` to expect the new `MergeOutcome` model; delete dataclass imports. Adjust printing/rendering helpers to use the new attribute names.
  4. **Feedback adjustments:** Ensure `feedback` flows (e.g., `ops feedback apply`) operate solely on `FeedbackAdjustmentEvent` from `domain/events.py`. Remove any lingering dataclasses and conversions.
  5. **Retriever contract:** Introduce `aijournal/domain/retrieval.py` with `RetrievalFilters` (immutable), `RetrievalMeta`, and `RetrievalResult` as `StrictModel`s. Update `src/aijournal/services/retriever.py` to:
     - Keep internal helper dataclasses (prefixed with `_`) if needed for performance.
     - Return the new domain `RetrievalResult` to callers (`chunks` + `meta`).
  6. **API consumers:** Verify chat CLI/API (`src/aijournal/services/chat.py`, `src/aijournal/services/chat_api.py`) and tests consume the strict retrieval models without additional casting.
  7. **Tests:** Update consolidation tests (`tests/test_cli_characterize.py`, `tests/test_cli_profile_apply.py`, `tests/services/test_capture.py`) to assert on the new strict models. Add regression coverage that serializes `MergeOutcome`/`RetrievalResult` to JSON and back.
  8. **Doc updates:** Document the “no dataclasses across IO boundaries” rule in `refactor3.md` (this section) and cross-link in `docs/refactor3_status.md`.
- **Acceptance criteria:**
  - `reports/data_model_out.txt` lists the new strict models instead of the dataclasses; remove dataclass entries or mark them module-private.
  - CLI/profile/chat code no longer imports dataclasses from `services/consolidator` or `services/retriever`.
  - New/updated schemas blessed; tests covering consolidation (`tests/test_cli_characterize.py`, `tests/test_cli_profile_apply.py`) and retrieval (`tests/services/test_retriever.py`, `tests/test_cli_chat.py`) pass.
  - Decision log updated with rationale.

### 17.4 Introduce Enumerations for Claim & Event Types

- **Problem:** Claim types/status/methods and preview actions are still declared as `Literal[...]` unions (`src/aijournal/domain/claims.py:15`, `src/aijournal/domain/events.py`). Literals work but provide poor error messages and anonymous enums in JSON schema.
- **Scope of change (fine-grained):**
  1. **Enum module:** Create `src/aijournal/domain/enums.py` exporting `ClaimType`, `ClaimStatus`, `ClaimMethod`, and `PreviewAction` (`StrEnum`). Include docstrings clarifying each value.
  2. **Domain updates:** Replace `Literal[...]` annotations in `domain/claims.py`, `domain/changes.py`, `domain/events.py`, and any other modules that hard-code the strings.
  3. **Service updates:** Update consolidator, profile commands, CLI helpers, and tests to compare against enum members (`ClaimType.HABIT`) instead of raw strings. When serializing (e.g., telemetry), use `.value` where necessary.
  4. **Prompts/examples:** Review `prompts/*.md` to ensure enumerated values still match the strings; no changes expected, but call it out in this plan for verification.
  5. **Schema regeneration:** Run the schema checker to confirm enums appear under the correct module names and that the exported JSON uses identical string values.
  6. **Docs:** Update `ARCHITECTURE.md` (claims section) to mention enums explicitly and link to the module for reference.
- **Acceptance criteria:**
  - `git grep "Literal['preference'"` returns zero matches outside of historical docs.
  - Updated prompts/examples validated via `tests/prompts/test_prompt_examples.py` prove the LLM response schema remains aligned.
  - Any FastAPI OpenAPI generation (if applicable) now exposes named enums.
  - Document the enum adoption in `docs/refactor3_status.md` and cross-reference in `ARCHITECTURE.md` (claims section).

### 17.5 Execution Instructions (All Work Packages)

- Always run `uv run pytest -q` and `uv run python scripts/check_schemas.py --bless` (after verifying schema diffs) before committing. The reviewer does not value backwards compatibility, so delete legacy files rather than deprecating unless a temporary alias is absolutely required.
- After each package, append a Decision Log row noting the date, package ID (17.1–17.4), summary, and impact.
- When all packages complete, re-run the fake-mode rehearsal (`AIJOURNAL_FAKE_OLLAMA=1`) covering capture → summarize → facts → profile suggest/apply → characterize → persona → pack → chat to guarantee no runtime path still references removed models.
- Suggested commit sequence (adjust if intermediate fixes are required):
  1. `refactor3: replace AdviceLLMResponse with AdviceCard across advise pipeline`
  2. `refactor3: remove SummaryMeta/PersonaCoreMeta and persist notes in ArtifactMeta`
  3. `refactor3: promote consolidator/retrieval surfaces to strict domain models`
  4. `refactor3: introduce StrEnum definitions for claim and preview types`
  5. `refactor3: refresh schemas, fixtures, and decision log`

  Each commit must leave `git status` clean and the test suite/schema checks green.

> **Reminder:** These work packages are prerequisites for claiming Refactor3 is fully realized. Do not close the refactor until each bullet in this section is resolved, tested, documented, and committed.
