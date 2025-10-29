# Refactor Plan v3 – Strict Data Model & Artifact Unification

> **MANDATORY CHECKPOINT RULE (DO NOT VIOLATE):** After **every** numbered sub-step: (1) finish the listed tasks, (2) run all required commands (always `uv run pytest`, plus any additional checks spelled out for that sub-step), (3) confirm a green result, and (4) commit only the files touched in that sub-step using a precise message. Never proceed to the next sub-step with uncommitted changes or failing tests.

This document is the authoritative runbook for the strict-schema consolidation of `aijournal`. Every automation or AI agent must begin here, follow the sequence exactly, and produce the specified commit cadence. No human intervention will occur between steps, so clarity and determinism are paramount.

---

## 0. Executive Summary

- **Mission:** Collapse duplicated “payload vs. derived” schemas, enforce strict structured output from the LLM, introduce versioned `Artifact[T]` envelopes, and keep capture/feedback privacy guarantees intact.
- **Why now:** The current landscape (see `scripts/data_model_report.py`) shows dozens of nearly identical Pydantic models and dataclasses. This hampers validation, complicates reasoning, and forces coercion layers. A strict, unified schema makes the system safer, easier to audit, and ready for autonomous agents.
- **Key Deliverables:**
  1. Part A – Pain points & duplication map (for context).
  2. Part B – Unified v2 data model (strict Pydantic class outlines + rationale).
  3. Part C – Migration plan (phased, with adapters, prompts, tests, risks).
  4. “Prompt Draft” for downstream agents ensuring they operate with these assumptions.

This runbook expands every part of the prior proposal into actionable, testable sub-steps with explicit commands, acceptance criteria, and commit checkpoints.

---

## 1. Reference Materials (Read/Refresh Before Stage 0)

| Path | Purpose |
| ---- | ------- |
| `README.md` | Product overview, CLI workflow, personas.
| `ARCHITECTURE.md` | Authoritative system design (pipelines, claims, persona, retrieval).
| `docs/workflow.md` | Daily operator flow; command ordering.
| `agents.md` | Live-mode rehearsal details, success criteria.
| `scripts/data_model_report.py` | Inventory script for Pydantic models/dataclasses.
| `refactor3.md` *(this file)* | Runbook – **do not modify structure without alignment**.

**Agents must confirm they have read/skimmed each document before executing Stage 0.**

---

## 2. Guiding Principles & Non-Negotiables

1. **Strict Structured Output:** LLM responses must already conform to the same Pydantic classes we persist. Missing required fields = hard schema failure (retry once via existing machinery, then abort).
2. **Privacy:** Claim provenance must never persist raw text excerpts; micro-facts may carry text during analysis but it must be stripped before persisting claims.
3. **Capture DTO Separation:** Keep `CaptureRequest` (public) distinct from `CaptureInput` (internal with `min_stage`/`max_stage`).
4. **Event Shapes:** Preserve rich preview events and lightweight feedback adjustments via a discriminated union.
5. **Artifact Versioning:** Persist derived outputs inside `Artifact[T]` envelopes with `schema: "v2"` and a human-readable `kind` identifier.
6. **Atomic Commits:** Each sub-step ends with a passing test suite and a dedicated commit.

---

## 3. Part A – Pain Points & Duplication (Context)

| Area | Duplication | Notes |
| ---- | ----------- | ----- |
| **Payload vs. Derived Models** | `MicroFact` vs. `ExtractedFactPayload`; `AdviceRecommendation` vs. `AdviceLLMRecommendation`; `ClaimProposal` vs. `ClaimProposalPayload`; `FacetProposal` vs. `FacetProposalPayload`; `DailySummary` vs. `DailySummaryResponse`; `ProfileSuggestions` vs. `ProfileSuggestionsResponse`/`SimpleProfileSuggestionsResponse`. | Same semantics expressed twice to distinguish “LLM produced” vs. “persisted”. Leads to conversion code and inconsistent validation. |
| **Sections & Entities** | `ingest_agent.IngestSection` vs. `models.authoritative.JournalSection`. | Only difference is presence of `para_index`. |
| **Evidence** | `ClaimSource`/`ClaimSourceSpan` vs. `FactEvidence`/`FactEvidenceSpan`. | Identical structure; fact span adds optional `text`. |
| **Change Events** | `ClaimPreviewEvent` (Pydantic) vs. `FeedbackAdjustment` (dataclass). | Both describe claim changes but with different payload carriers. |
| **Conflicts/Signatures** | `services.consolidator.ClaimConflict` (dataclass) vs. `models.derived.ClaimConflictPayload` (Pydantic). | Redundant representations. |
| **Chat Responses** | `ChatLLMResponse`, `ChatTurn`, `ChatCitation`. | Mixed dataclass/Pydantic; inconsistent layering. |
| **Chunks & Index Meta** | `ChunkManifestChunk` vs. `RetrievedChunk`; duplicate meta blocks. | Same entity at different lifecycle stages. |
| **Capture DTOs** | `CaptureRequest` vs. `CaptureInput`. | Intentionally similar but extra fields on the latter; we retain both. |
| **Meta Blocks** | Many derived artifacts repeat `(llm_model, prompt_path, prompt_hash, created_at)`. | Needs central `ArtifactMeta`. |
| **Package Layout** | Domain/payload split via class names rather than module organization. | We will reorganize into `aijournal.domain`, `aijournal.artifacts`, `aijournal.api`, etc. |

These duplications drive the refactor’s scope (see Part B/C for the target state).

---

## 4. Part B – Unified v2 Data Model (Strict)

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
from typing import Generic, TypeVar, Literal
from pydantic import Field

T = TypeVar("T")

class ArtifactMeta(StrictModel):
    created_at: TimestampStr
    model: str | None = None
    prompt_path: str | None = None
    prompt_hash: str | None = None
    char_per_token: float | None = None
    sources: dict[str, str] | None = None
    notes: dict[str, str] | None = None

class Artifact(Generic[T], StrictModel):
    kind: str
    schema: Literal["v2"] = "v2"
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
        for source in prov.sources:
            for span in source.spans:
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
class ProfileUpdateBatch(StrictModel):
    batch_id: str
    created_at: TimestampStr
    date: ISODateStr
    inputs: list[dict]
    proposals: ProfileUpdateProposals
    meta: ArtifactMeta
    preview: dict | None = None  # {"claim_events": [...], "interview_prompts": [...]}

class FeedbackBatch(StrictModel):
    batch_id: str
    created_at: TimestampStr
    events: list[FeedbackAdjustmentEvent]
```

### 4.7 Facts & Summaries

```python
# aijournal/domain/facts.py
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
    preview: dict | None = None

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

class ChatRequest(StrictModel):
    question: str
    top: int | None = None
    tags: list[str] | None = None
    source: list[str] | None = None
    date_from: ISODateStr | None = None
    date_to: ISODateStr | None = None
    session_id: str | None = None
    save: bool = True
    feedback: Literal['up','down'] | None = None

class ChatResponse(StrictModel):
    answer: str
    citations: list[ChatCitation] = []
    clarifying_question: str | None = None
    telemetry: dict[str, object] = {}
    timestamp: TimestampStr

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

The migration is orchestrated via the staged checklist in section 6. Highlights:

1. Introduce strict base classes and artifact envelope primitives without behavioural change.
2. Collapse duplicated models (sections, evidence, proposals, facts, summaries) into strict domain modules.
3. Update prompts & structured-output validation to enforce strict schemas.
4. Unify change events and feedback handling.
5. Align chunk/index/chat/advice surfaces with the new domain structure.
6. Preserve capture DTO boundaries.
7. Wrap artifacts in `Artifact[T]`, supply compatibility shims, regenerate documentation and examples.
8. Run end-to-end rehearsal, publish migration notes, and prepare release guidance.

Each stage is decomposed below with explicit commands and acceptance criteria.

---

## 6. Stage-by-Stage Execution Checklist

> **Remember:** after each sub-step, run required commands → verify success → commit immediately.

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

### Stage 1 – Strict Base & Artifact Infrastructure

**1.1 StrictModel Introduction**
- Create `aijournal/common/base.py` with `StrictModel`.
- Update existing base models (e.g., `src/aijournal/models/base.py`) to re-export or subclass `StrictModel`.
- Touch only minimal files to compile.
- Tests: `uv run pytest`.
- Commit: `refactor3: introduce strict base model`.

**1.2 ArtifactMeta & Helpers**
- Add `aijournal/common/meta.py` (`ArtifactMeta`, `Artifact[T]`, `LLMResult[T]`).
- Implement `aijournal/io/artifacts.py` with helper functions: `save_artifact`, `load_artifact`, `read_legacy_or_artifact`.
- No existing artifacts converted yet.
- Tests: `uv run pytest`; `pre-commit run --all-files` (clean formatting).
- Commit: `refactor3: add artifact envelope primitives`.

### Stage 2 – Sections & Evidence

**2.1 Unified Section Model**
- Introduce `aijournal/domain/journal.py` with `Section`, `NormalizedEntry`, `NormalizedEntity`.
- Replace `IngestSection`/`JournalSection` references in ingest agent, normalization pipeline, tests.
- Ensure serialization parity by regenerating fixtures if required.
- Tests: `uv run pytest`.
- Commit: `refactor3: unify journal section schema`.

**2.2 Evidence Consolidation & Privacy Enforcement**
- Create `aijournal/domain/evidence.py` with `Span`, `SourceRef`.
- Replace `ClaimSourceSpan`, `FactEvidenceSpan`, `ClaimSource`, `FactEvidence` references.
- Add helper `strip_provenance_text(source_ref)` that blanks `span.text`.
- Update claim persistence to call the helper; add unit test verifying attempted persistence with text raises error.
- Tests: `uv run pytest` (include targeted privacy test).
- Commit: `refactor3: standardize evidence spans and enforce privacy`.

### Stage 3 – Strict Proposals, Facts, Summaries

**3.1 Remove Sketch/Payload Models**
- Delete `ClaimSketch`, `ClaimProposalPayload`, `FacetProposalPayload`, `ProfileSuggestionsResponse`, `SimpleProfileSuggestionsResponse`.
- Add `aijournal/domain/changes.py` with `ClaimAtomInput`, `ClaimProposal`, `FacetChange`, `ProfileUpdateProposals`.
- Update pipelines (`commands/profile.py`, `pipelines/characterize.py`, etc.) to use strict models directly.
- Ensure validators treat missing required fields as hard errors; adjust error messaging to bubble up validation failures.
- Tests: `uv run pytest`.
- Commit: `refactor3: enforce strict claim and facet proposals`.

**3.2 Strict Facts & Summaries**
- Remove `ExtractedFactPayload`, `ExtractedFactsResponse`, `DailySummaryResponse`.
- Add `aijournal/domain/facts.py` with `MicroFact`, `MicroFactsFile`, `DailySummary` (strict).
- Update `pipelines/facts.py`, `pipelines/summarize.py`, CLI commands to rely on these models.
- Tests: `uv run pytest`.
- Commit: `refactor3: tighten facts and summaries schemas`.

**3.3 Prompt Updates & Structured Output Contracts**
- Edit `prompts/profile_suggest.md`, `prompts/characterize.md`, `prompts/extract_facts.md`, `prompts/summarize_day.md` with JSON examples reflecting strict schema.
- Document failure behaviour in each prompt header.
- Tests: `uv run pytest`; optionally run smoke commands in fake mode (document commands run in commit message body).
- Commit: `refactor3: align prompts with strict schema outputs`.

### Stage 4 – Claim Events & Feedback

**4.1 Discriminated Union for Events**
- Add `aijournal/domain/events.py` with `ClaimPreviewEvent`, `FeedbackAdjustmentEvent`, `ClaimChangeEvent` union.
- Update `models/derived.py`, `services/consolidator.py`, `services/feedback.py`, and pipelines to use the new types.
- Keep preview data (signature/conflict) intact; ensure serialization uses `kind` discriminator.
- Tests: `uv run pytest`; add snapshot test verifying preview JSON structure.
- Commit: `refactor3: unify claim change event models`.

**4.2 Feedback Batches Formalization**
- Add `FeedbackBatch` model; update feedback storage to serialize events via the union.
- Ensure `aijournal ops feedback apply` still adjusts strengths correctly; add/adjust tests accordingly.
- Tests: `uv run pytest`; run `AIJOURNAL_FAKE_OLLAMA=1 uv run aijournal ops feedback apply` on fixture data.
- Commit: `refactor3: convert feedback batches to strict schema`.

### Stage 5 – Retrieval, Chat, Advice

**5.1 Chunk & Index Unification**
- Create `aijournal/domain/index.py` with `Chunk`, `RetrievedChunk`, `IndexMeta`.
- Update `pipelines/index.py`, `services/retriever.py`, tests, and chunk manifest serializers.
- Begin using `Artifact[list[Chunk]]` and `Artifact[IndexMeta]` for persisted index outputs (others remain legacy for now).
- Tests: `uv run pytest`; run `AIJOURNAL_FAKE_OLLAMA=1 uv run aijournal ops index rebuild` on sample data if fixtures exist.
- Commit: `refactor3: unify chunk and index schema`.

**5.2 Chat & Advice DTOs**
- Replace `ChatLLMResponse`/`ChatTurn` with strict `ChatResponse` and `ChatCitation` in `aijournal/api/chat.py`.
- Collapse `AdviceLLMRecommendation` into `AdviceRecommendation`; adjust `AdviceCard` accordingly.
- Update CLI commands, services, tests, and transcripts to new structures.
- Tests: `uv run pytest`; optional CLI smoke tests in fake mode (`uv run aijournal chat ...`).
- Commit: `refactor3: streamline chat and advice responses`.

### Stage 6 – Capture Separation Reinforced

**6.1 DTO Relocation & Validation**
- Move/define `CaptureRequest` and `CaptureInput` in `aijournal/api/capture.py`, ensuring `CaptureInput` inherits `CaptureRequest`.
- Confirm CLI uses only `CaptureRequest` (no `min_stage/max_stage` leakage).
- Add unit test verifying serialized CLI schema lacks stage fields.
- Tests: `uv run pytest`.
- Commit: `refactor3: formalize capture request/input split`.

### Stage 7 – Artifact Adoption & Compatibility

**7.1 Wrap Derived Artifacts in Artifact[T]**
- Convert persisted YAML/JSON (summaries, microfacts, persona core, profile updates, feedback, index metadata, chat transcripts, packs) to `Artifact[T]` envelopes.
- Update IO helpers to read legacy format → convert to `Artifact[T]` on load; optionally provide CLI migration command.
- Regenerate fixtures and golden outputs.
- Tests: `uv run pytest`; ensure new artifact files include `schema: "v2"` and `kind` fields.
- Commit: `refactor3: adopt artifact envelopes for derived data`.

**7.2 Compatibility Layer**
- Add `aijournal/compat/refactor3.py` exposing legacy class names for one release (e.g., `ExtractedFactPayload = MicroFact`).
- Update main code to import new domain modules directly; only external callers use compat layer if necessary.
- Tests: `uv run pytest`; `pre-commit run --all-files`.
- Commit: `refactor3: add legacy compatibility aliases`.

### Stage 8 – Documentation & Examples

**8.1 Documentation Updates**
- Update `README.md`, `ARCHITECTURE.md`, `docs/workflow.md`, `agents.md` to describe strict schema behaviour, artifact envelopes, privacy enforcement, and event unions.
- Link to this runbook (`refactor3.md`) as the canonical reference for the refactor.
- Tests: `uv run pytest`; run Markdown lint (`pre-commit run --all-files`) if configured.
- Commit: `refactor3: document strict schema architecture`.

**8.2 Example Artifact Regeneration**
- Regenerate sample packs, persona files, index manifests, microfact outputs to reflect new format (fake mode allowed).
- Store them under `tests/fixtures/` or `docs/examples/` as appropriate.
- Tests: `uv run pytest`.
- Commit: `refactor3: refresh examples for schema v2`.

### Stage 9 – Validation & Release Prep

**9.1 End-to-End Rehearsal**
- Commands (fake mode acceptable):
  - `export AIJOURNAL_FAKE_OLLAMA=1`
  - `uv run aijournal init --path /tmp/aijournal_refactor3`
  - Capture fixture entries (`uv run aijournal capture --text ...`), run `status`, `chat`, `advise`, `export pack`, `ops feedback apply`.
- Verify all generated artifacts include `schema: "v2"`/`kind`, and claim provenance has no `span.text`.
- Tests: `uv run pytest`; optionally `pre-commit run --all-files`.
- Commit: `refactor3: verify end-to-end workflow under schema v2`.

**9.2 Completion Log & Changelog**
- Create `docs/refactor3_status.md` summarizing executed steps, test results, artifacts verified.
- Update `CHANGELOG.md` with migration notes (strict schema, artifact envelopes, compatibility layer).
- Tests: `uv run pytest`.
- Commit: `refactor3: finalize strict schema release notes`.

**9.3 Release Checklist (Optional)**
- Draft instructions for tagging, packaging, communication (no actual tag pushed).
- Tests: `uv run pytest` to ensure no drift.
- Commit (if file changed): `refactor3: prepare release checklist`.

---

## 7. Prompt & Validation Contracts (Detailed)

### 7.1 Structured Output Settings

- All pipelines must pass `response_model=<StrictModel subclass>` to the structured-output runner.
- On schema failure: print validation errors, include sample expected object, retry once, then raise `StructuredOutputError` with actionable guidance.
- Add logging hook to capture invalid payloads for debugging (store under `derived/logs/structured_failures/`).

### 7.2 Command Expectations

| Command | Required Output Schema | Notes |
| ------- | ---------------------- | ----- |
| `aijournal ops pipeline summarize` | `DailySummary` | `bullets`, `highlights`, `todo_candidates` must be lists (empty allowed). |
| `aijournal ops pipeline extract-facts` | `MicroFactsFile` | Each fact needs `evidence.entry_id` and ≥1 span; spans may contain `text`. |
| `aijournal ops profile suggest` | `ProfileUpdateProposals` | Each `claim` is full `ClaimAtomInput`; `FacetChange.value` required for `set`/`merge`. |
| `aijournal ops pipeline characterize` | `ProfileUpdateProposals` + interview prompts | Same strict schema. |
| `aijournal advise` | `AdviceCard` | All recommendations present; arrays may be empty but not omitted. |
| Chat surfaces | `ChatResponse` | `citations` list, `timestamp` ISO string, optional `clarifying_question`. |

### 7.3 Prompt Snippets

Include example JSON fragments in each prompt to illustrate the strict schema. Example for profile suggest (place in `prompts/profile_suggest.md`):

```json
{
  "claims": [
    {
      "claim": {
        "type": "habit",
        "subject": "self",
        "predicate": "prefers_morning_focus",
        "value": "prefers early deep work sessions",
        "statement": "I prefer to do deep work before noon",
        "scope": {"domain": "work", "context": ["weekday"], "conditions": []},
        "strength": 0.72,
        "status": "tentative",
        "method": "inferred",
        "user_verified": false,
        "review_after_days": 120
      },
      "normalized_ids": ["2025-10-26-planning"],
      "evidence": [
        {
          "entry_id": "2025-10-26-planning",
          "spans": [{"type": "excerpt", "index": 0, "start": 42, "end": 118, "text": "..."}]
        }
      ],
      "manifest_hashes": ["abc123"],
      "rationale": "Deep work scheduling appears repeatedly."
    }
  ],
  "facets": []
}
```

---

## 8. Testing Matrix

| Stage/Sub-step | Required Commands | Purpose |
| -------------- | ----------------- | ------- |
| All | `uv run pytest` | Baseline regression suite. |
| 1.2 | `pre-commit run --all-files` | Ensure new utilities respect linting. |
| 2.2 | Custom unit test verifying provenance text removal. |
| 3.x | `AIJOURNAL_FAKE_OLLAMA=1` pipeline smoke tests (optional but recommended). |
| 4.2 | CLI feedback apply in fake mode to ensure event union works. |
| 5.1 | `aijournal ops index rebuild` in fake mode to validate chunk schema. |
| 7.1 | Golden file diff review (ensure only envelope changes). |
| 9.1 | Full workflow rehearsal in temp workspace. |

Record results (pass/fail, command output summary) in commit messages or `docs/refactor3_status.md` as indicated.

---

## 9. Risk Register & Mitigations

| Risk | Mitigation |
| ---- | ---------- |
| LLM fails to output strict schema initially | Enhanced prompts, structured-output retry, CI catch. |
| Claim provenance accidentally stores text | Validators + targeted tests; redaction helper. |
| Artifact conversion corrupts existing data | Provide legacy readers, add migration CLI, keep backups. |
| Compatibility breaks third-party scripts | Offer import aliases for one release; document changes prominently. |
| Commit discipline lapses | This document + automation enforcement. |

---

## 10. Logging & Evidence Collection

- Store structured-output failures under `derived/logs/structured_failures/` (JSON per command).
- For each major stage, append a brief note to `docs/refactor3_status.md` describing commands run and outcomes.
- Keep copies of regenerated artifacts for review in PRs.

---

## 11. Commit Message Template

Use the following structure (customize the summary):

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
- `docs/refactor3_status.md` summarises execution.
- `CHANGELOG.md` documents schema v2 introduction and upgrade instructions.
- Compatibility layer present; prompts/documentation updated.
- Optional release checklist prepared (if required by maintainers).

---

## 13. Appendix A – “Prompt Draft” for Delegated Agents

Use this snippet verbatim when spawning new agents responsible for any portion of the refactor:

> **Role**: Senior software architect & data modeler for `aijournal`.
>
> **Inputs**: `README.md`, `ARCHITECTURE.md`, `docs/workflow.md`, `agents.md`, `refactor3.md`, data model inventory (`reports/data_model_out.txt`).
>
> **Objective**: Implement the strict v2 data model, replacing payload/response twins with unified domain classes, adopting artifact envelopes, enforcing privacy, and preserving capture/event semantics.
>
> **Hard Requirements**:
> 1. Strict structured output (no sketches); missing required fields = failure.
> 2. Capture DTO separation (`CaptureRequest` public, `CaptureInput` internal).
> 3. Evidence privacy – no `span.text` in persisted claim provenance.
> 4. Distinct preview vs. feedback events via discriminated union.
> 5. Tests must pass after each `refactor3` sub-step; commit immediately.
>
> **Deliverables**: Completed code per `refactor3.md` checklist, updated documentation, migration utilities, compatibility aliases, changelog entry, and status log.

---

## 14. Appendix B – Command Quick Reference

| Purpose | Command |
| ------- | ------- |
| Run tests | `uv run pytest` |
| Lint/format | `pre-commit run --all-files` |
| Inventory models | `uv run python scripts/data_model_report.py` |
| Fake-mode pipeline (example) | `AIJOURNAL_FAKE_OLLAMA=1 uv run aijournal ops pipeline summarize --date 2025-10-26` |
| Fake-mode capture rehearsal | `AIJOURNAL_FAKE_OLLAMA=1 uv run aijournal capture --text "..." --tags sample` |

---

## 15. Absolute Final Reminder

At every sub-step boundary:

1. **Finish assigned tasks exactly as written.**
2. **Run required commands (always `uv run pytest`, plus extras).**
3. **Confirm success (no tolerated failures).**
4. **Commit immediately with an informative message.**

Any agent that cannot satisfy these conditions must halt and report back rather than improvising. This discipline keeps the refactor auditable and safe for automation.

