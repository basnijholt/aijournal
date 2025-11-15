# Prompt 3 Design Summary: Unified Profile Update Prompt & DTOs

## Overview

This document describes the design for collapsing `profile_suggest.md` and `characterize.md` into a single unified **Profile Update** prompt and its corresponding input/output DTOs.

**Goal**: Replace two sequential LLM passes (profile_suggest → characterize) with one richer, context-aware pass per date that:
- Reads normalized entries, daily summary, microfacts, and current profile/claims.
- Emits claims, facet updates, and interview prompts in a single LLM call.
- Integrates seamlessly with the existing `ProfileUpdateProposals`, `ClaimProposal`, and `FacetChange` downstream flows.

---

## 1. Unified Prompt Design

### Prompt File: `prompts/profile_update.md`

**Name**: Profile Update (unified)
**Purpose**: Single authoritative LLM-driven profile update pass per day
**Inputs**: Normalized entries, daily summary, microfacts, current profile, current claims
**Outputs**: JSON with `claims`, `facets`, `interview_prompts`

#### Prompt Structure

```markdown
You are the **Profile Update Agent** for aijournal.
Your job is to review a day's normalized journal entries along with its summary, extracted facts,
and the current persona, then propose grounded profile updates that keep the self-model accurate,
explainable, and reviewable by humans.

The model receiving this prompt knows nothing about aijournal besides what you read here.
Take your time to understand the task, reason through the evidence, and emit a single JSON object
**with exactly the keys `claims`, `facets`, and `interview_prompts`**.

Do not add prose, markdown fences, or trailing commentary.
If you have nothing grounded to add, return the empty payload
`{ "claims": [], "facets": [], "interview_prompts": [] }`.

## Your Task

Analyze the provided journal entry, its summary, and extracted facts. Propose incremental profile
updates that reflect durable patterns and new insights. Produce only `claims`, `facets`, and
`interview_prompts` arrays; the system handles ID generation, provenance tracking, and review workflows.

## Mental Model

- Claims capture specific statements about the user (e.g., habits, values, goals).
- Claims must follow the `ClaimAtomInput` shape; the backend adds `id` and `provenance`.
- Facets are higher-level knobs inside the persona profile such as `planning.routines` or
  `values_motivations`.
- Each facet update either `set`s a new value (string or list of strings) or `remove`s an
  outdated one.
- Interview prompts surface the smallest set of follow-up questions (≤20 words) needed to
  confirm or clarify high-impact uncertainties.
- Every proposal must reference concrete evidence (normalized entry IDs and paragraph indices).
- Skip proposals entirely when evidence is weak or missing.

Think like a careful researcher: read the inputs, form hypotheses, check the entries, and
document only what the evidence supports.

## Reasoning Checklist

1. Read `PROFILE_JSON` and `CLAIMS_JSON` to understand the current baseline.
2. Scan the entry's `summary` field (title, bullets, highlights, todo_candidates) for signals.
3. Review the entry's `sections` (key semantic blocks) and tags.
4. Examine today's `MICROFACTS_JSON` for extracted behavioral insights.
5. Collect candidate signals from paragraphs and structured fields.
6. Treat figurative, metaphorical, or speculative language as context only; only promote it
   into a claim when the entry states the fact plainly and unambiguously.
7. Strengthen or refine an existing claim or facet when new evidence confirms it.
8. Introduce a new claim or facet only when entries reveal a durable new pattern.
9. Remove a facet when the evidence shows it no longer applies.
10. Score each claim using the strength calibration ladder below; default to 0.55 when in doubt.
11. Generate interview prompts for ambiguities that need human input.
12. Document each accepted insight using the schema exactly as specified and drop anything
    that lacks evidence or duplicates existing statements.

## Strength Calibration Reference

- **0.30–0.40**: Single ambiguous mention or inference only; exploratory.
- **0.50–0.60**: One or two clear mentions **or** a single self-report.
- **0.70–0.80**: Three to five entries showing a pattern **or** strong self-report plus
  behavioral evidence.
- **0.85–0.95**: Five or more consistent entries **or** user-verified claims.
- **0.95–1.00**: Immutable facts only (e.g., birthdate) or formally verified truths.
- Default to 0.55 when uncertain and note ambiguity in the rationale.

---

## Output Schema (copy faithfully)

```json
{
  "claims": [
    {
      "type": "preference|value|goal|boundary|trait|habit|aversion|skill",
      "statement": "Readable sentence (≤160 chars)",
      "subject": "who or what the claim refers to (optional, ≤80 chars)",
      "predicate": "relationship or attribute (optional, ≤80 chars)",
      "value": "string value (optional, ≤160 chars)",
      "strength": 0.0-1.0 (optional, defaults to 0.55 if omitted),
      "status": "accepted|tentative|rejected (optional, defaults to tentative)",
      "method": "self_report|inferred|behavioral (optional, defaults to inferred)",
      "scope_domain": "domain context like 'work' or 'personal' (optional)",
      "scope_context": ["weekday", "solo"] (optional list of context tags),
      "scope_conditions": [] (optional list of conditional qualifiers),
      "reason": "≤25 word justification citing the evidence",
      "evidence_entry": "normalized-entry-id (optional)",
      "evidence_para": 0
    }
  ],
  "facets": [
    {
      "path": "values_motivations.recurring_theme",
      "operation": "set" | "remove",
      "value": "string or list of strings when operation is set",
      "reason": "≤25 word justification (optional)",
      "evidence_entry": "normalized-entry-id (optional)",
      "evidence_para": 0
    }
  ],
  "interview_prompts": [
    "≤20 word question referencing claim or profile.path"
  ]
}
```

### Allowed Values

- `type`: preference, value, goal, boundary, trait, habit, aversion, skill.
- `status`: accepted, tentative, rejected.
- `method`: self_report, inferred, behavioral.
- `operation`: set, remove.

### Constraints

- Subject, predicate, value, strength, status, method, scope fields are **optional** for claims.
- Reason and evidence fields are **optional** (can be null/0).
- Facet `value` must be a string or list of strings (never objects).
- Keep `reason` ≤25 words and `interview_prompts` ≤20 words.
- Keep `statement` ≤160 chars, `subject`/`predicate` ≤80 chars, `value` ≤160 chars.
- `evidence_para` is the paragraph index (0-based integer, default 0).
- When omitted, strength defaults to 0.55, status to tentative, method to inferred, scope fields
  to empty.

## ⚠️ Critical Constraints (Violations = Rejection)

1. Facet `operation` must be `set` or `remove` (never `merge`).
2. Facet `value` must be a string or list of strings (never objects).
3. Statement ≤160 chars, subject/predicate ≤80 chars, value ≤160 chars, reason ≤25 words,
   interview prompt ≤20 words.

---

## Examples

### Example A – Grounded Update with Summary & Fact Context

Suppose today's entry mentions "shipped `/auto` automation workflow with comprehensive safeguards",
the summary highlights "Automation finally handles repetitive ticket triage", and a microfact
states "validates automation changes with manual smoke tests".

```json
{
  "claims": [
    {
      "type": "habit",
      "statement": "Invests time in automation workflows that replace repetitive coding tasks.",
      "subject": "automation",
      "predicate": "invests_in",
      "value": "Builds automation workflows to eliminate repetitive coding tasks.",
      "strength": 0.75,
      "method": "behavioral",
      "reason": "Summary + entry detail shipping `/auto`; microfact confirms validation practice.",
      "evidence_entry": "2025-10-31-automation",
      "evidence_para": 0
    }
  ],
  "facets": [
    {
      "path": "planning.quality_guardrails",
      "operation": "set",
      "value": "Validates automation changes with manual smoke tests before rollout.",
      "reason": "Microfact and entry both describe cautious review before enabling.",
      "evidence_entry": "2025-10-31-automation",
      "evidence_para": 1
    }
  ],
  "interview_prompts": [
    "What safeguards gate `/auto` from production use?"
  ]
}
```

### Example B – Nothing to Add

```json
{ "claims": [], "facets": [], "interview_prompts": [] }
```

### Example C – Invalid

- Never emit `operation: "merge"` for facets (only `set` or `remove`).
- Never use object values for facets (only strings or lists of strings).
- Never write interview prompts longer than 20 words.
- Never invent evidence entries or dates.

Any violation will cause the proposal to be rejected downstream.

---

## Failure Handling

Return `{"claims": [], "facets": [], "interview_prompts": []}` when **any** of the following occur:

- `ENTRIES_JSON` is malformed or missing required fields.
- Entries contain only metadata with no summaries/sections/paragraphs to ground evidence.
- Evidence contradicts itself across entry fields and cannot be resolved without operator input.
- No new information exists beyond what `CLAIMS_JSON` and `PROFILE_JSON` already capture.

Do not include explanations; the downstream system will log the failure for review.

---

## Input Data (read-only context)

DATE: $date

ENTRIES_JSON: $entries_json

SUMMARY_JSON: $summary_json

MICROFACTS_JSON: $microfacts_json

PROFILE_JSON: $profile_json

CLAIMS_JSON: $claims_json

---

## Final Instruction

Review the checklist, verify all constraints are satisfied, and emit the JSON object now.
Output only the final payload.
```

#### Key Differences from the Old Prompts

1. **Unified Output Schema**: Single call produces `claims`, `facets`, and `interview_prompts`.
2. **Richer Context**: Includes `SUMMARY_JSON` (day bullets, highlights, todos) and `MICROFACTS_JSON`
   (extracted facts with confidence).
3. **Simplified Reasoning**: Merges the best practices from both `profile_suggest` (evidence-focused
   claims) and `characterize` (facets + interview prompts).
4. **No Prose Output**: Same as both original prompts—JSON only, no markdown.

---

## 2. Input Data Schema

### What Gets Serialized

The unified prompt receives five main JSON payloads:

#### 2.1 ENTRIES_JSON
**Format**: Array of normalized entries for the target date.

```json
[
  {
    "id": "2025-10-31-automation",
    "date": "2025-10-31",
    "title": "Shipped /auto workflow",
    "summary": "Finalized `/auto` workflow and merged safeguards",
    "sections": [
      {
        "title": "Automation Design",
        "content": "Completed the design phase for the `/auto` workflow..."
      }
    ],
    "tags": ["automation", "engineering", "quality"],
    "projects": ["infrastructure"],
    "mood": "energized",
    "paragraphs": [
      "Completed the design phase for the `/auto` workflow, which will automate repetitive coding tasks.",
      "Implemented safeguards to prevent unsafe automation from rolling out."
    ]
  }
]
```

**Source**: Loaded from `data/journal/YYYY/MM/DD/*.md` and normalized by the capture pipeline.
**Fields Used**: `id`, `summary`, `sections`, `paragraphs`, `tags`, `mood`, `projects`.

#### 2.2 SUMMARY_JSON
**Format**: Single daily summary object (or null if unavailable).

```json
{
  "day": "2025-10-31",
  "bullets": [
    "Finalized `/auto` workflow and merged safeguards",
    "Documented capture refactor risks and mitigation plan"
  ],
  "highlights": [
    "Automation finally handles repetitive ticket triage"
  ],
  "todo_candidates": [
    "Run `/auto` smoke tests with real data tomorrow"
  ]
}
```

**Source**: `derived/summaries/YYYY-MM-DD.yaml` (produced by summarize stage).
**Availability**: Optional; may be null if summary hasn't been run yet.
**Why**: Provides structured high-level signals (key outcomes, notable moments, follow-ups).

#### 2.3 MICROFACTS_JSON
**Format**: Array of micro-facts extracted for the target date (or empty if unavailable).

```json
{
  "facts": [
    {
      "id": "auto-validation-practice",
      "statement": "Validates automation changes with manual smoke tests before rollout.",
      "confidence": 0.8,
      "first_seen": "2025-10-31",
      "last_seen": "2025-10-31"
    },
    {
      "id": "automation-goal",
      "statement": "Aims to eliminate repetitive coding tasks through automation.",
      "confidence": 0.75,
      "first_seen": "2025-10-31",
      "last_seen": "2025-10-31"
    }
  ],
  "claim_proposals": []
}
```

**Source**: `derived/microfacts/YYYY-MM-DD.yaml` (produced by facts extraction stage).
**Availability**: Optional; may be empty if facts extraction hasn't run.
**Why**: Provides lower-level behavioral insights already extracted and confidence-scored.

#### 2.4 PROFILE_JSON
**Format**: Current self-profile dictionary.

```json
{
  "planning": {
    "routines": "Morning focus blocks 8–10 AM, weekly retrospectives",
    "quality_guardrails": "Code review before merge, automated tests pass"
  },
  "values_motivations": {
    "recurring_theme": "Automation reduces manual toil"
  },
  "habits": {
    "deep_work": "Protects mornings for uninterrupted focus"
  }
}
```

**Source**: `profile/self_profile.yaml` (authoritative profile store).
**Availability**: Always available (initialized on `aijournal init`).
**Why**: Provides context for what profile already exists; the LLM uses this to identify gaps,
confirm patterns, or suggest updates.

#### 2.5 CLAIMS_JSON
**Format**: Array of existing claim atoms.

```json
{
  "claims": [
    {
      "id": "claim-001",
      "type": "habit",
      "subject": "focus blocks",
      "predicate": "maintains",
      "value": "Protects 8:00–10:00 on weekdays for deep work",
      "statement": "Maintains morning focus blocks on weekdays.",
      "strength": 0.85,
      "status": "accepted",
      "method": "behavioral",
      "scope": {
        "domain": "work",
        "context": ["weekday"],
        "conditions": []
      },
      "provenance": {
        "sources": [
          {
            "entry_id": "2025-10-29-focus",
            "spans": [{ "type": "para", "index": 0 }]
          }
        ],
        "first_seen": "2025-10-29",
        "last_updated": "2025-10-30",
        "observation_count": 3
      }
    }
  ]
}
```

**Source**: `profile/claims.yaml` (authoritative claims store).
**Availability**: Always available (initialized on `aijournal init`).
**Why**: Provides context for existing claims; the LLM uses this to decide whether to
reinforce, modify, or leave unchanged.

---

## 3. Output DTOs (Pydantic Models)

All output DTOs already exist in `src/aijournal/domain/prompts.py` and require **no changes**
to support the unified prompt:

### 3.1 PromptClaimItem
**Location**: `aijournal.domain.prompts.PromptClaimItem`

```python
class PromptClaimItem(StrictModel):
    """Lightweight claim item that LLM emits (no system metadata)."""
    type: ClaimType
    statement: str  # ≤160 chars
    subject: str | None = None  # ≤80 chars
    predicate: str | None = None  # ≤80 chars
    value: str | None = None  # ≤160 chars
    strength: float | None = None  # 0.0–1.0
    status: ClaimStatus | None = None
    method: ClaimMethod | None = None
    scope_domain: str | None = None
    scope_context: list[str] | None = None
    scope_conditions: list[str] | None = None
    reason: str | None = None  # ≤25 words
    evidence_entry: str | None = None
    evidence_para: int = 0
```

**Validation**:
- Enforces `statement` as non-empty and ≤160 chars.
- Enforces `reason` ≤25 words.
- Defaults: `strength=0.55`, `status=tentative`, `method=inferred`.

### 3.2 PromptFacetItem
**Location**: `aijournal.domain.prompts.PromptFacetItem`

```python
class PromptFacetItem(StrictModel):
    """Lightweight facet change that LLM emits (no system metadata)."""
    path: str
    operation: FacetOperation  # set | remove
    value: Any | None = None
    reason: str | None = None  # ≤25 words
    evidence_entry: str | None = None
    evidence_para: int = 0
```

**Validation**:
- Enforces `path` as non-empty.
- Enforces `reason` ≤25 words.
- Enforces `value` required when `operation=set`.
- Validates that facet `value` is a string or list of strings (never objects).

### 3.3 PromptProfileUpdates
**Location**: `aijournal.domain.prompts.PromptProfileUpdates`

```python
class PromptProfileUpdates(StrictModel):
    """Container for LLM-emitted profile updates (lightweight DTOs only)."""
    claims: list[PromptClaimItem] = []
    facets: list[PromptFacetItem] = []
    interview_prompts: list[str] = []
```

**Status**: Already supports `interview_prompts` (added in prior work); no changes needed.

---

## 4. Conversion Functions

The existing conversion function also requires **no changes**:

### 4.1 convert_prompt_updates_to_proposals
**Location**: `aijournal.domain.prompts.convert_prompt_updates_to_proposals`

```python
def convert_prompt_updates_to_proposals(
    prompt_updates: PromptProfileUpdates,
    *,
    normalized_ids: list[str],
    manifest_hashes: list[str],
) -> ProfileUpdateProposals:
    """Convert lightweight prompt DTOs to full domain models with system metadata."""
```

**Output**: Returns `ProfileUpdateProposals` (which contains `claims`, `facets`, `interview_prompts`).

**Behavior**:
- Converts each `PromptClaimItem` to `ClaimProposal` (adds provenance, timestamps, etc.).
- Converts each `PromptFacetItem` to `FacetChange` (adds method, confidence, evidence).
- Passes through `interview_prompts` unchanged.

---

## 5. Implementation Checklist

### Prompt & Configuration
- [ ] Create `prompts/profile_update.md` (new unified prompt, see §1 above).
- [ ] Remove `prompts/profile_suggest.md` (legacy, replaced by unified).
- [ ] Remove `prompts/characterize.md` (legacy, replaced by unified).

### Command & Pipeline
- [ ] Create `src/aijournal/pipelines/profile_update.py` (new unified pipeline).
  - Reads normalized entries, summary, microfacts, profile, claims for a date.
  - Invokes the LLM with the unified prompt.
  - Converts response to `ProfileUpdateProposals`.
  - Writes to `derived/pending/profile_updates/<date>.yaml`.
- [ ] Rename `run_profile_suggest` → `run_profile_update` in `src/aijournal/commands/profile.py`
  (or keep name if wrapping the new pipeline).
- [ ] Update `graceful_profile_suggest` wrapper in `src/aijournal/services/capture/graceful.py`
  to call the new unified pipeline (or rename to `graceful_profile_update`).
- [ ] Update `ProfileStage4` to use the unified pipeline instead of old suggest/characterize calls.

### CLI & Configuration
- [ ] Update capture pipeline to call the unified profile update stage (replacing stages 4 & 5).
- [ ] Optionally rename `ops profile suggest` → `ops profile update` (or keep for compatibility
  if wrapping the unified pipeline).
- [ ] Remove or deprecate `ops pipeline characterize` command.
- [ ] Update help text and documentation.

### Testing & Validation
- [ ] Add/update tests to verify the unified pipeline:
  - Reads normalized entries, summary, microfacts, profile, claims.
  - Emits claims, facets, interview_prompts as expected.
  - Handles missing optional inputs (summary, microfacts) gracefully.
- [ ] Update fake fixtures in `src/aijournal/fakes.py`:
  - Add `fake_profile_update` (or rename existing `fake_profile_proposals`).
  - Ensure deterministic output for `AIJOURNAL_FAKE_OLLAMA=1` mode.
- [ ] Run end-to-end capture tests to confirm the workflow still works.

### Documentation
- [ ] Update `README.md` and `ARCHITECTURE.md` to describe the single unified profile update stage.
- [ ] Update `docs/workflow.md` to show the new command/pipeline sequence.
- [ ] Add a note in `CHANGELOG.md` describing the unification and removal of old stages/commands.

---

## 6. Downstream Compatibility

### ProfileUpdateProposals
The existing `ProfileUpdateProposals` DTO (in `aijournal.domain.changes`) is used by:
- `ops profile apply` (applies proposals to the profile/claims).
- `ops profile review` (human review workflow).
- Artifact serialization (writes to `derived/pending/profile_updates/`).

**No changes needed**: The unified prompt output flows directly into this DTO and is
immediately compatible with all downstream code.

### ProfileUpdatePreview & Artifact Handling
Existing code in `src/aijournal/models/` that builds preview summaries and artifacts
will work unchanged because:
- The unified prompt emits the same `PromptProfileUpdates` shape.
- The conversion to `ProfileUpdateProposals` is identical.
- The serialization to YAML is identical.

---

## 7. Edge Cases & Graceful Degradation

### Missing Optional Inputs
The unified prompt gracefully handles missing optional inputs:

| Input | If Missing | Behavior |
|-------|-----------|----------|
| ENTRIES_JSON | Fail (required) | Return error |
| SUMMARY_JSON | May be null | LLM skips summary signals; uses entry paragraphs only |
| MICROFACTS_JSON | May be empty | LLM skips fact-based context; uses entry text only |
| PROFILE_JSON | Should not happen | Always available; fail if missing |
| CLAIMS_JSON | Should not happen | Always available; fail if missing |

**Implementation**: The unified pipeline should:
1. Load entries (fail if not found).
2. Load summary if available; pass null to LLM if not.
3. Load microfacts if available; pass empty array to LLM if not.
4. Always load profile and claims (fail if not found).
5. Call the LLM with all available context.

---

## 8. Summary

### Prompt File Changes
- **New**: `prompts/profile_update.md` (unified, replaces both old prompts).
- **Delete**: `prompts/profile_suggest.md`, `prompts/characterize.md`.

### DTO Changes
- **No changes**: Existing DTOs in `aijournal.domain.prompts` already support the unified output.
  - `PromptClaimItem`, `PromptFacetItem`, `PromptProfileUpdates` (with `interview_prompts`).
  - `convert_prompt_updates_to_proposals` conversion function.

### Input Data Schema
- **ENTRIES_JSON**: Normalized entries (already used by old prompts).
- **SUMMARY_JSON**: Daily summary from summarize stage (new).
- **MICROFACTS_JSON**: Extracted facts from facts stage (new).
- **PROFILE_JSON**: Current self-profile (already used).
- **CLAIMS_JSON**: Existing claims (already used).

### Command & Pipeline Changes
- New unified `profile_update` pipeline in `src/aijournal/pipelines/profile_update.py`.
- Updated commands in `src/aijournal/commands/profile.py` to use the unified pipeline.
- Updated capture stage 4 to call the unified pipeline (replacing old stages 4 & 5).
- Graceful wrapper in `src/aijournal/services/capture/graceful.py`.

### Downstream Compatibility
- All existing code that consumes `ProfileUpdateProposals` (apply, review, artifacts) works unchanged.
- No breaking changes to existing APIs or data formats.

---

## 9. Example Unified Pipeline Call Flow

```
1. Capture pipeline detects changed date (e.g., 2025-10-31).
2. Stage 3: Run summarize → produces `derived/summaries/2025-10-31.yaml`.
3. Stage 3: Run facts extraction → produces `derived/microfacts/2025-10-31.yaml`.
4. Stage 4 (unified profile update):
   a. Load normalized entries from `data/journal/2025/10/31/*.md`.
   b. Load summary from `derived/summaries/2025-10-31.yaml`.
   c. Load microfacts from `derived/microfacts/2025-10-31.yaml`.
   d. Load profile from `profile/self_profile.yaml`.
   e. Load claims from `profile/claims.yaml`.
   f. Call LLM with unified `profile_update.md` prompt.
   g. Receive PromptProfileUpdates (claims, facets, interview_prompts).
   h. Convert to ProfileUpdateProposals.
   i. Write to `derived/pending/profile_updates/2025-10-31.yaml`.
   j. (Optional) Auto-apply if `--apply-profile=auto`.
5. Stage 5 (optional review): Human reviews + applies via `ops profile apply`.
```

---

## 10. Testing Strategy

### Unit Tests
- Test prompt DTO validation (statement length, reason word count, etc.).
- Test conversion functions (PromptClaimItem → ClaimProposal, etc.).
- Test graceful handling of missing optional inputs.

### Integration Tests
- Run unified pipeline on a sample date with all inputs present.
- Run unified pipeline on a sample date with missing optional inputs (summary, microfacts).
- Verify output structure and content conform to expected shape.
- Verify fake LLM mode produces deterministic output.

### End-to-End Tests
- Full capture pipeline on a test workspace.
- Verify unified profile update stage runs instead of old stages.
- Verify claims, facets, and interview prompts appear in output.

---

**End of Prompt 3 Design Summary**
