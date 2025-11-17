# RAG-Enhanced Profile Update: Design & Implementation Plan

**Status**: Approved for Implementation
**Date**: 2025-01-16
**Context**: Zero external users, retrieval infrastructure exists

---

## Executive Summary

### What We're Building
Enhanced `profile_update` stage that uses Retrieval-Augmented Generation (RAG) to:
1. **Strengthen claims** with historical evidence (every entry)
2. **Consolidate facets** from patterns (when threshold met)
3. **Maintain evidence-based profile** automatically

### Why This Matters
- **Current problem**: Claims work but profile stays empty forever
- **User pain**: Manual `self_profile.yaml` editing, no pattern detection, system doesn't "learn"
- **Impact**: Chat/advice can't personalize (no profile context), L1 persona pack is empty
- **Solution**: Automatic profile building through RAG-powered pattern detection

### Success Criteria
After 50 entries:
- ✅ Profile has 5-10 populated facets
- ✅ Claims cite historical chunks (50%+ with evidence)
- ✅ Facet proposals 80%+ acceptance rate
- ✅ Profile usable by chat/advice (non-empty, accurate)

---

## 1. The Problem

### 1.1 Observer vs. Knower

**Current State**: System is a good **observer** but poor **knower**

```
Observer (Claims) ✅ Working:
Entry: "Focused well this morning" → Claim: "Prefers morning focus" (strength: 0.55)

Knower (Profile) ❌ Broken:
Profile: { planning: {}, habits: {}, values_motivations: {} }  ← Empty!
```

### 1.2 Why This Matters

**For Context Packing** (L1-L4 levels):
- L1 persona core needs compressed profile state (not 100 individual claims)
- Chat needs "when does user work best?" → Can't answer (no `habits.deep_work_timing`)
- Advice needs "what are their values?" → Can't answer (no `values_motivations.themes`)

**For Human Understanding**:
- User opens `self_profile.yaml` and sees... nothing useful
- User has 50 claims but no patterns identified
- Manual editing doesn't scale

**For Long-Term Growth**:
- Claims accumulate but never consolidate into wisdom
- System doesn't "learn" patterns from history
- No cross-entry pattern detection

### 1.3 Real-World Example

**Scenario**: User writes about morning focus in 5 different entries over 2 weeks

**Current Behavior**:
```
Entry 1 → Claim: "morning focus" (strength: 0.5)
Entry 2 → Claim: "morning focus" (strength: 0.5)  // Independent
Entry 3 → Claim: "morning focus" (strength: 0.5)  // No connection
...
Profile: {} // Still empty!
```

**Desired Behavior**:
```
Entry 1 → Claim: "morning focus" (strength: 0.5)
Entry 2 → RAG finds Entry 1 → Claim strengthened (strength: 0.65)
Entry 3 → RAG finds Entries 1+2 → Claim strengthened (strength: 0.75)
Entry 5 → Consolidation triggered (5 entries = pattern!)
         → Facet: habits.deep_work_timing = "Mornings 8-10 AM"
Profile: { habits: { deep_work_timing: "Mornings 8-10 AM" } } ✅
```

---

## 2. The Solution

### 2.1 Architecture Overview

```
Entry → RAG retrieves history → Claims (strengthened) + Facets (when threshold) → Review → Profile
```

### 2.2 Two-Track System

**Track 1: Claims** (Every Entry)
```
Input: Current entry + Retrieved chunks (via RAG)
Process:
  1. Extract claims from entry
  2. Search history for supporting/contradicting evidence
  3. Strengthen claims when pattern detected (+0.1 to +0.2 strength)
  4. Cite chunk_ids in evidence
Output: Claim proposals (some strengthened by historical evidence)
```

**Track 2: Facets** (Every 10 Claims)
```
Trigger: new_claims >= 10?
If yes:
  1. Answer questions via RAG (Q1: current focus? Q2: habits? Q3: values?)
  2. Each question searches all chunks for patterns
  3. Propose facets when evidence threshold met (planning=2, habits=3, traits=5)
Output: Facet proposals (consolidated patterns from claims)
```

### 2.3 Question-Driven Consolidation

Instead of dumping all claims into prompt, ask **specific questions**:

```markdown
Q1: What is user currently working on?
   → search_chunks("current project focus goals", k=10)
   → If 2+ matches: planning.current_focus = "..."

Q2: When does user do best work?
   → search_chunks("morning afternoon productivity peak", k=10)
   → If 3+ matches: habits.deep_work_timing = "..."

Q3: What values recur in their writing?
   → search_chunks("values important principles themes", k=10)
   → If 5+ matches: values_motivations.recurring_themes = [...]

Q4: What routines appear regularly?
   → search_chunks("habits routines patterns schedule", k=10)
   → If 3+ matches: habits.routines = "..."

Q5: What are their long-term goals?
   → search_chunks("goals objectives milestones future", k=10)
   → If 3+ matches: goals.objectives = [...]

Q6: What personality traits are evident?
   → search_chunks("personality traits preferences style", k=10)
   → If 5+ matches: traits.* = ...
```

**Evidence Thresholds** (encoded in prompt):
- `planning.*` facets: 2+ chunks
- `habits.*`, `goals.*` facets: 3+ chunks
- `values_motivations.*`, `traits.*` facets: 5+ chunks

**Why This Works**:
- Questions are human-readable (self-documenting)
- Questions map to facet paths (structured output)
- RAG retrieves only relevant claims (not all 100)
- Threshold enforced per question category

---

## 3. Implementation Design

### 3.1 Data Flow

```
┌────────────────────┐
│  Capture Entry     │
└─────────┬──────────┘
          │
          ▼
┌──────────────────────────────────────────────┐
│          profile_update Stage                │
│                                              │
│  PREPARE:                                    │
│   • Load entry, summary, microfacts          │
│   • Load current profile & claims            │
│   • Run 4 retrieval queries (parallel)       │
│     - "current projects goals focus"         │
│     - "habits routines timing patterns"      │
│     - "values principles themes"             │
│     - "personality traits preferences"       │
│   • Dedupe, limit to 40 chunks               │
│   • Check: new_claims >= 10?                 │
│                                              │
│  INVOKE:                                     │
│   • Build prompt with RETRIEVED_CHUNKS_JSON  │
│   • Add CONSOLIDATION_TRIGGERED flag         │
│   • LLM generates:                           │
│     - Claims (strengthened by chunks)        │
│     - Facets (if consolidation triggered)    │
│     - Interview prompts                      │
│                                              │
│  PERSIST:                                    │
│   • Save ProfileUpdateBatch                  │
│   • Update consolidation metadata            │
│     (claim_count, timestamp)                 │
└─────────┬────────────────────────────────────┘
          │
          ▼
┌────────────────────┐
│  Review & Apply    │
└────────────────────┘
```

### 3.2 Retrieval Strategy

**Pre-compute searches** before LLM call (not tool calling):

```python
RETRIEVAL_QUERIES = [
    "current projects goals focus priorities",
    "habits routines patterns morning afternoon timing",
    "values principles themes important care about",
    "personality traits preferences style decisions",
]

# For each query:
retriever.search(query, k=10)

# Total: ~40 chunks (~4-6K tokens)
```

**Why pre-retrieval instead of tool calling?**
- ✅ Simpler (single LLM call)
- ✅ Deterministic (same queries every time)
- ✅ Faster (parallel retrieval, not sequential)
- ✅ Works today (no function calling required)
- ✅ Can add tool calling later if needed (Phase 2)

### 3.3 Prompt Structure

**Enhanced `prompts/profile_update.md`**:

```markdown
You are the Profile Update Agent.

# PART 1: Extract Claims (Always)

Analyze today's entry AND retrieved historical chunks.

INPUTS:
- <ENTRIES_JSON>: Today's normalized entry
- <SUMMARY_JSON>: Daily summary
- <MICROFACTS_JSON>: Extracted facts
- <PROFILE_JSON>: Current profile state
- <CLAIMS_JSON>: Existing claims
- <RETRIEVED_CHUNKS_JSON>: 30-40 relevant chunks from history

PROCESS:
1. Extract observations from TODAY'S entry
2. Search RETRIEVED_CHUNKS for supporting/contradicting evidence
3. If 2+ chunks support observation: increase strength (+0.1 to +0.2)
4. If chunks contradict: note in rationale, consider status=tentative
5. Cite BOTH entry_id AND chunk_ids in evidence

EXAMPLE:
Entry: "Focused well this morning from 8-10"
Retrieved chunks: 5 previous entries mentioning "morning focus 8-10"
→ Claim: "Prefers deep work in morning (8-10 AM)"
  strength: 0.75 (instead of 0.55 for single observation)
  evidence_entry: "2025-01-16_morning-work"
  evidence_chunks: ["2025-01-10_...", "2025-01-08_...", ...]

---

# PART 2: Consolidate Facets (Conditional)

{{ if CONSOLIDATION_TRIGGERED }}

You now have {{ new_claims_count }} new claims since last consolidation.
Answer these questions using RETRIEVED_CHUNKS and CLAIMS_JSON:

## CURRENT STATE (planning.*)

Q1: What is the user currently working on or focused on?
   Search for: recent project mentions, goal statements
   Threshold: 2+ chunks OR 1 very explicit statement
   Facet: planning.current_focus
   Example: "aijournal v0.3 live rehearsal"

Q2: What are their current blockers or challenges?
   Search for: obstacles, challenges, stuck, blocked
   Threshold: 2+ chunks mentioning consistent blocker
   Facet: planning.blockers
   Example: ["Testing infrastructure", "Documentation gaps"]

## ESTABLISHED PATTERNS (habits.*)

Q3: When does the user do their best deep work?
   Search for: time-of-day + productivity/focus mentions
   Threshold: 3+ chunks showing consistent pattern
   Facet: habits.deep_work_timing
   Example: "Mornings 8-10 AM"

Q4: What routines appear regularly?
   Search for: recurring activities, schedules, rituals
   Threshold: 3+ chunks across different weeks
   Facet: habits.routines
   Example: "Morning coffee → email → deep work block"

## IDENTITY (values_motivations.*, traits.*)

Q5: What values or themes recur in their writing?
   Search for: "important", "value", "care about", "principles"
   Threshold: 5+ chunks showing consistent theme
   Facet: values_motivations.recurring_themes
   Example: ["Autonomy", "Craftsmanship", "Impact"]

Q6: What personality traits are evident?
   Search for: behavioral patterns, decision style, preferences
   Threshold: 5+ chunks showing trait-relevant behavior
   Facet: traits.* (specify which trait and value)
   Example: traits.decision_style = "analytical and data-driven"

## EVIDENCE REQUIREMENTS:
- planning.*: 2+ chunks OR 1 very explicit statement
- habits.*, goals.*: 3+ chunks spanning ≥2 weeks
- values_motivations.*, traits.*: 5+ chunks with thematic consistency

## STABILITY RULES:
- To SET new facet: Meet base threshold
- To UPDATE existing facet: Need 50% more evidence (e.g., 3 → 5 chunks)
- To REMOVE facet: Need strong contradicting evidence (5+ chunks)

OUTPUT FORMAT:
{
  "claims": [...],
  "facets": [
    {
      "path": "planning.current_focus",
      "operation": "set",
      "value": "aijournal v0.3 live rehearsal",
      "evidence_chunk_ids": ["2025-01-14_...", "2025-01-15_...", "2025-01-16_..."],
      "reason": "3 entries this week explicitly mention live rehearsal as priority"
    }
  ],
  "interview_prompts": [...]
}

{{ endif }}
```

---

## 4. Code Changes

### 4.1 Models

**Restore facets field** (`src/aijournal/domain/prompts.py`):
```python
class PromptProfileUpdates(StrictModel):
    """Container for LLM-emitted profile updates."""
    claims: list[PromptClaimItem] = Field(default_factory=list)
    facets: list[PromptFacetItem] = Field(default_factory=list)  # RESTORED
    interview_prompts: list[str] = Field(default_factory=list)

class PromptFacetItem(StrictModel):
    """Lightweight facet change that LLM emits."""
    path: str
    operation: FacetOperation  # set | remove
    value: Any | None = None
    reason: str | None = None
    evidence_chunk_ids: list[str] = Field(default_factory=list)  # NEW
```

**Restore facets field** (`src/aijournal/domain/changes.py`):
```python
class ProfileUpdateProposals(StrictModel):
    """Aggregate container for proposed claim updates."""
    claims: list[ClaimProposal] = Field(default_factory=list)
    facets: list[FacetChange] = Field(default_factory=list)  # RESTORED
    interview_prompts: list[str] = Field(default_factory=list)

class FacetChange(StrictModel):
    """Proposed change to a profile facet."""
    path: str  # e.g., "habits.deep_work_timing"
    operation: FacetOperation  # set | remove
    value: Any | None = None
    evidence: list[SourceRef] = Field(default_factory=list)
    rationale: str | None = None
```

**Enhanced SourceRef** (`src/aijournal/domain/evidence.py` or where it lives):
```python
class SourceRef(StrictModel):
    entry_id: str | None = None
    paragraph_index: int | None = None
    manifest_hash: str | None = None
    chunk_id: str | None = None  # NEW: link to retrieved chunk
    score: float | None = None  # NEW: retrieval similarity score
```

### 4.2 Command Changes

**Enhanced prepare phase** (`src/aijournal/commands/profile_update.py`):
```python
@dataclass
class ProfileUpdatePrepared:
    # ... existing fields ...
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)  # NEW
    consolidation_triggered: bool = False  # NEW

def _prepare(ctx: RunContext, date: str) -> ProfileUpdatePrepared:
    # ... existing code loads entries, summary, microfacts, profile, claims ...

    # NEW: ALWAYS retrieve chunks
    retriever = Retriever(ctx.workspace, ctx.config)
    chunks = []

    RETRIEVAL_QUERIES = [
        "current projects goals focus priorities",
        "habits routines patterns morning afternoon timing",
        "values principles themes important care about",
        "personality traits preferences style decisions",
    ]

    for query in RETRIEVAL_QUERIES:
        result = retriever.search(query, k=10)
        chunks.extend(result.chunks)

    # Dedupe by chunk_id, sort by score, limit to 40
    seen = set()
    deduped = []
    for chunk in sorted(chunks, key=lambda c: c.score, reverse=True):
        if chunk.chunk_id not in seen:
            seen.add(chunk.chunk_id)
            deduped.append(chunk)
    retrieved_chunks = deduped[:40]

    # NEW: Check consolidation threshold
    new_claims_since_last = count_new_claims_since_last_consolidation(ctx)
    consolidation_triggered = (new_claims_since_last >= 10)

    return ProfileUpdatePrepared(
        # ... existing fields ...
        retrieved_chunks=retrieved_chunks,
        consolidation_triggered=consolidation_triggered,
    )

def _invoke(ctx: RunContext, prepared: ProfileUpdatePrepared) -> Artifact[ProfileUpdateBatch]:
    # ... existing variable setup ...

    # NEW: Add retrieved chunks to prompt variables
    variables["RETRIEVED_CHUNKS_JSON"] = json.dumps({
        "metadata": {
            "queries": RETRIEVAL_QUERIES,
            "total_chunks_returned": len(prepared.retrieved_chunks),
            "new_claims_since_last": count_new_claims_since_last_consolidation(ctx),
        },
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "date": chunk.date,
                "text": chunk.text,
                "tags": list(chunk.tags) if chunk.tags else [],
                "source_type": chunk.source_type,
                "score": round(chunk.score, 3),
            }
            for chunk in prepared.retrieved_chunks
        ]
    }, indent=2)

    # NEW: Add consolidation flag
    variables["CONSOLIDATION_TRIGGERED"] = prepared.consolidation_triggered

    # Invoke LLM
    llm_proposals = invoke_structured_llm(
        prompt_path="prompts/profile_update.md",
        variables=variables,
        response_model=PromptProfileUpdates,
        agent_name="profile_update",
        config=ctx.config,
    )

    # Convert to full proposals
    proposals = convert_prompt_updates_to_proposals(
        llm_proposals,
        normalized_ids=prepared.normalized_ids,
        manifest_hashes=prepared.manifest_hashes,
        entry_hash_lookup=prepared.entry_hash_lookup,
    )

    # NEW: If consolidation ran and facets proposed, update metadata
    if prepared.consolidation_triggered and proposals.facets:
        update_consolidation_metadata(
            ctx.workspace,
            claim_count=count_all_claims(ctx.workspace),
            timestamp=claim_timestamp,
        )

    # ... rest of existing code ...
```

**Consolidation metadata helpers** (new functions):
```python
def count_new_claims_since_last_consolidation(ctx: RunContext) -> int:
    """Count claims added since last consolidation run."""
    metadata_path = ctx.workspace / "derived" / "profile_update_meta.yaml"
    if not metadata_path.exists():
        return count_all_claims(ctx.workspace)  # Never run before

    meta = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    last_count = meta.get("last_consolidation", {}).get("claim_count", 0)
    current_count = count_all_claims(ctx.workspace)
    return current_count - last_count

def update_consolidation_metadata(
    workspace: Path,
    claim_count: int,
    timestamp: str,
) -> None:
    """Update metadata after consolidation run."""
    metadata_path = workspace / "derived" / "profile_update_meta.yaml"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "last_consolidation": {
            "timestamp": timestamp,
            "claim_count": claim_count,
        }
    }

    metadata_path.write_text(
        dump_yaml(data, sort_keys=False),
        encoding="utf-8",
    )

def count_all_claims(workspace: Path) -> int:
    """Count total accepted + tentative claims."""
    claims_path = workspace / "profile" / "claims.yaml"
    if not claims_path.exists():
        return 0

    claims_data = yaml.safe_load(claims_path.read_text(encoding="utf-8"))
    claims = claims_data.get("claims", [])
    return sum(
        1 for c in claims
        if c.get("status") in ["accepted", "tentative"]
    )
```

### 4.3 Conversion Functions

**Update conversion** (`src/aijournal/domain/prompts.py`):
```python
def convert_prompt_updates_to_proposals(
    prompt_updates: PromptProfileUpdates,
    *,
    normalized_ids: list[str],
    manifest_hashes: list[str],
    entry_hash_lookup: Mapping[str, str] | None = None,
) -> ProfileUpdateProposals:
    """Convert lightweight prompt DTOs to full domain models."""

    # Convert claims (existing logic)
    claims = [
        convert_prompt_claim_to_proposal(
            item,
            normalized_ids=...,
            manifest_hashes=...,
        )
        for item in prompt_updates.claims
    ]

    # NEW: Convert facets
    facets = [
        convert_prompt_facet_to_change(item)
        for item in prompt_updates.facets
    ]

    return ProfileUpdateProposals(
        claims=claims,
        facets=facets,
        interview_prompts=prompt_updates.interview_prompts,
    )

def convert_prompt_facet_to_change(item: PromptFacetItem) -> FacetChange:
    """Convert lightweight facet DTO to full FacetChange."""
    # Build evidence from chunk_ids
    evidence = [
        SourceRef(chunk_id=chunk_id)
        for chunk_id in item.evidence_chunk_ids
    ]

    return FacetChange(
        path=item.path,
        operation=item.operation,
        value=item.value,
        evidence=evidence,
        rationale=item.reason,
    )
```

### 4.4 Review & Apply

**Restore facet processing** (`src/aijournal/commands/profile.py` and `cli.py`):
- Un-delete facet application loop
- Un-delete facet display in review UI
- Un-delete facet validation logic

(These were removed in the earlier cleanup - just restore them)

---

## 5. Testing Strategy

### 5.1 Unit Tests

```python
def test_retrieval_queries_execute():
    """Test that retrieval queries run successfully."""
    ctx = make_test_context()
    prepared = _prepare(ctx, date="2025-01-16")

    assert len(prepared.retrieved_chunks) > 0
    assert len(prepared.retrieved_chunks) <= 40
    assert all(chunk.score > 0 for chunk in prepared.retrieved_chunks)

def test_consolidation_threshold_logic():
    """Test consolidation triggers at correct threshold."""
    ctx = make_test_context()

    # Below threshold
    seed_claims(count=5)
    assert count_new_claims_since_last_consolidation(ctx) == 5

    # At threshold
    seed_claims(count=5)  # total: 10
    assert count_new_claims_since_last_consolidation(ctx) == 10

    # After consolidation
    update_consolidation_metadata(ctx.workspace, claim_count=10, timestamp="2025-01-16T10:00:00Z")
    assert count_new_claims_since_last_consolidation(ctx) == 0

def test_claim_strengthening_with_retrieval():
    """Test that claims get stronger when historical evidence exists."""
    # Index 5 entries about morning focus
    for i in range(5):
        index_entry(f"2025-01-{10+i}", text="Focused well this morning")

    # New entry should strengthen claim
    result = run_profile_update("2025-01-16", text="Great morning focus today")

    batch = load_latest_batch()
    claim = next(c for c in batch.claims if "morning" in c.statement.lower())

    # Strength boosted by historical evidence
    assert claim.strength > 0.6

    # Cites historical chunks
    chunk_evidence = [e for e in claim.evidence if e.chunk_id]
    assert len(chunk_evidence) >= 2

def test_facet_consolidation_only_when_triggered():
    """Test facets only proposed when threshold met."""
    ctx = make_test_context()

    # Below threshold: no facets
    seed_claims(count=5)
    result = run_profile_update("2025-01-16")
    assert len(result.facets) == 0

    # At threshold: facets proposed
    seed_claims(count=5)  # total: 10
    result = run_profile_update("2025-01-17")
    assert len(result.facets) > 0
```

### 5.2 Integration Tests

```python
def test_end_to_end_profile_building():
    """Test full workflow: entries → claims → consolidation → facets."""
    # Capture 10 entries about morning focus
    for i in range(10):
        capture_entry(
            date=f"2025-01-{10+i}",
            text=f"Productive morning session {i}. Focused 8-10 AM on coding."
        )

    # 11th entry triggers consolidation
    result = capture_entry(
        date="2025-01-21",
        text="Another great morning focus session 8-10 AM"
    )

    # Load latest batch
    batch = load_latest_batch()

    # Should have claims
    assert len(batch.claims) > 0
    morning_claim = next(c for c in batch.claims if "morning" in c.statement.lower())
    assert morning_claim.strength > 0.7  # Strengthened
    assert len(morning_claim.evidence) > 1  # Cites chunks

    # Should have facets (consolidation triggered)
    assert len(batch.facets) > 0

    # Should have deep_work_timing facet
    timing_facet = next(
        (f for f in batch.facets if "deep_work" in f.path),
        None
    )
    assert timing_facet is not None
    assert "morning" in timing_facet.value.lower()
    assert len(timing_facet.evidence) >= 3  # Threshold met
```

---

## 6. Success Metrics

### 6.1 Quality Metrics

**Claim Quality**:
- ✅ 50%+ of claims cite retrieval evidence (historical chunks)
- ✅ Average claim strength +0.15 vs. baseline (single-entry claims)
- ✅ <10% duplicate claims (RAG catches similar existing claims)

**Facet Quality**:
- ✅ 100% of facets meet evidence threshold (2-5+ chunks)
- ✅ <20% user rejection rate (facets are accurate)
- ✅ 5+ facets populated after 50 entries
- ✅ Profile categories filled: planning, habits, values_motivations

**Evidence Quality**:
- ✅ All facets cite specific chunk_ids (verifiable)
- ✅ All chunk_ids are valid (exist in index)
- ✅ Retrieval scores reasonable (>0.5 for relevant chunks)

### 6.2 Performance Metrics

**Latency**:
- ✅ profile_update latency <7s (p50)
- ✅ profile_update latency <10s (p95)
- ✅ Retrieval latency <500ms (4 queries)

**Resource Usage**:
- ✅ Token usage <12K input tokens
- ✅ No OOM errors (memory safe)
- ✅ Consolidation frequency ~10-15 entries

### 6.3 User Experience Metrics

**Adoption**:
- ✅ 80%+ of consolidation batches reviewed
- ✅ 60%+ of facet proposals accepted

**Value**:
- ✅ Profile has 10+ facets after 100 entries
- ✅ Manual profile edits decrease (fewer needed)
- ✅ Chat uses profile facets in responses

---

## 7. Implementation Checklist

### Phase 1: Restore Facets Infrastructure (2-3 hours)
- [ ] Restore `facets` field in `PromptProfileUpdates` (domain/prompts.py)
- [ ] Restore `facets` field in `ProfileUpdateProposals` (domain/changes.py)
- [ ] Add `PromptFacetItem` model with `evidence_chunk_ids` field
- [ ] Restore `convert_prompt_facet_to_change()` function
- [ ] Add `chunk_id` and `score` fields to `SourceRef`
- [ ] Update all code that was removed in facet cleanup:
  - [ ] `commands/profile.py` - facet application loop
  - [ ] `cli.py` - facet display in review
  - [ ] `pipelines/profile_update.py` - facet processing
  - [ ] `services/capture/utils.py` - facet proposals
- [ ] Add tests for facet model validation

### Phase 2: Add Retrieval (2-3 hours)
- [ ] Add `retrieved_chunks` and `consolidation_triggered` to `ProfileUpdatePrepared`
- [ ] Update `_prepare()` to run 4 retrieval queries
- [ ] Dedupe and limit chunks to 40
- [ ] Add `count_new_claims_since_last_consolidation()` function
- [ ] Add `update_consolidation_metadata()` function
- [ ] Add `count_all_claims()` helper
- [ ] Update `_invoke()` to add `RETRIEVED_CHUNKS_JSON` to variables
- [ ] Add `CONSOLIDATION_TRIGGERED` flag to variables
- [ ] Call `update_consolidation_metadata()` when facets proposed
- [ ] Create `derived/profile_update_meta.yaml` structure

### Phase 3: Prompt Engineering (3-4 hours)
- [ ] Add `<RETRIEVED_CHUNKS_JSON>` section to profile_update.md
- [ ] Update Part 1 (claims) with retrieval instructions
- [ ] Add Part 2 (facets) with 6 questions
- [ ] Add evidence thresholds (planning=2, habits=3, traits=5)
- [ ] Add stability rules (50% more for updates)
- [ ] Add 5-7 concrete examples for claims + facets
- [ ] Test prompt with fake data
- [ ] Iterate on wording for clarity

### Phase 4: Testing (3-4 hours)
- [ ] Test retrieval query execution
- [ ] Test consolidation threshold logic
- [ ] Test claim strengthening with chunks
- [ ] Test facets only when triggered
- [ ] Test end-to-end workflow (10 entries → consolidation)
- [ ] Test with real journal entries
- [ ] Verify retrieval quality
- [ ] Check facet accuracy
- [ ] All 332+ tests pass

### Phase 5: Documentation (1 hour)
- [ ] Update ARCHITECTURE.md with RAG enhancement
- [ ] Update docs/workflow.md with consolidation info
- [ ] Add inline code comments
- [ ] Update CHANGELOG.md

**Total Estimated Time**: 13-17 hours

---

## 8. Why This Design

### 8.1 No Configuration
- **Retrieval always on**: Infrastructure exists, why wouldn't we use it?
- **Threshold hardcoded**: 10 is reasonable default, can iterate if needed
- **No toggles**: Single correct code path (zero users = zero legacy)

### 8.2 No Backward Compatibility
- **Zero users**: Just regenerate derived data
- **Simplify code**: No version checks, no migrations
- **Fast iteration**: Change schema freely

### 8.3 Pre-Retrieval Not Tool Calling
- **Works today**: No function calling required
- **Deterministic**: Same queries every time (reproducible)
- **Fast**: Parallel queries (not sequential LLM calls)
- **Simple**: Single LLM call (lower complexity)
- **Future-proof**: Can add tool calling later (Phase 2)

### 8.4 Threshold-Triggered Not Time-Based
- **Matches usage**: Sporadic entries (not daily logs)
- **Evidence-based**: Trigger when enough observations accumulated
- **Flexible**: Works whether you write daily or weekly

### 8.5 Question-Driven Not Free Exploration
- **Deterministic**: Same questions → same structure
- **Transparent**: Questions are human-readable intent
- **Structured**: Maps directly to profile schema
- **Token-efficient**: Retrieve relevant subset, not all claims

---

## 9. Future Enhancements (Phase 2)

**If this proves successful**, consider:

### Agentic Tool Calling
- LLM decides what to search for (not pre-defined queries)
- Can explore unexpected patterns
- Requires function calling support in Ollama model

### Claim Clustering
- Auto-detect themes via embeddings
- Group related claims
- Present clusters as facet candidates

### Contradiction Detection
- Find conflicting claims
- Surface as interview prompts
- Help resolve inconsistencies

---

## Summary

**Goal**: Make profile_update use RAG to strengthen claims + consolidate facets

**Approach**:
- Pre-computed retrieval (4 queries, 40 chunks)
- Question-driven consolidation (6 questions)
- Threshold trigger (10 new claims)
- Evidence-based (all changes cite chunks)

**Why Simple**:
- Zero users = no legacy constraints
- Just build it right
- Single code path

**Implementation**: ~15 hours
**Value**: Profile becomes useful, chat/advice improve, system "knows" user

---

**This is the missing piece that transforms aijournal from a note-taker into an intelligent assistant that truly knows you.**
