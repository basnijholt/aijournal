# aijournal Capture Pipeline Risk Analysis

**Date:** 2025-11-01
**Status:** ⚠️ **CRITICAL RISKS IDENTIFIED**
**Reviewed Components:** Capture pipeline, consolidation logic, LLM prompts, profile mutation paths

_Related documents: consult `RISK_ANALYSIS_GPT5.md` for complementary
architecture-focused findings and `RISK_DISCUSSION.md` for the joint review._

---

## Executive Summary

**FINDING:** Yes, there is **significant risk** that a single trivial `aijournal capture` command can corrupt or degrade the authoritative profile documents (`profile/claims.yaml`, `profile/self_profile.yaml`).

The combination of:
- Auto-apply by default (no human review gate)
- Weak consolidation math (logarithmic weight saturation)
- Optimistic LLM prompt defaults (strength 0.55 "when uncertain")
- Global persona rebuilds (token budget displacement)
- No minimum evidence thresholds

...creates conditions for **silent profile degradation** over time, where well-established claims can be diluted or displaced by weak, speculative, or contradictory observations from trivial entries.

---

## Critical Vulnerabilities

### 1. Auto-Apply is the Default (`src/aijournal/api/capture.py:26`)

```python
apply_profile: Literal["auto", "review"] = "auto"  # ❌ RISKY DEFAULT
```

**Impact:**
- Every `aijournal capture` command **automatically applies** profile changes without human review
- Users must explicitly opt-in to safety with `--apply-profile=review`
- No diff shown, no confirmation prompt, no rollback mechanism

**Risk Level:** 🔴 **CRITICAL**

---

### 2. Consolidation Math Saturates Too Early (`src/aijournal/services/consolidator.py:215-236`)

```python
n_prev = provenance.observation_count     # e.g., 100 observations
w_prev = min(1.0, math.log1p(n_prev))    # log1p(100) = 4.6 → CAPPED AT 1.0!
w_obs = 1.0                               # new observation ALWAYS weight 1.0
merged_strength = (w_prev * prev_strength + w_obs * signal) / (w_prev + w_obs)
```

**The Flaw:**
- Logarithmic weight **saturates at 1.0** after ~2.7 observations
- A claim with **10 observations** and a claim with **10,000 observations** get the **same weight** (1.0)
- Each new observation gets **equal weight** to the entire history

**Attack Example:**
```
Existing claim: strength=0.82, observations=100 → w_prev=1.0
Trivial entry:  strength=0.30, observations=1   → w_obs=1.0

Result: (1.0×0.82 + 1.0×0.30) / 2.0 = 0.56  ❌ 32% strength DROP from ONE entry!
```

**Risk Level:** 🔴 **CRITICAL**

---

### 3. No Minimum Evidence Threshold

**Current behavior:**
- A **single journal entry** can create a new claim
- LLM prompt says "default to 0.55 when uncertain" rather than "return empty if uncertain"
- No validation that claim has sufficient supporting evidence

**Impact:**
- Speculative claims pollute `profile/claims.yaml`
- Noise accumulates over time
- Token budget pressure in persona core

**Risk Level:** 🟠 **HIGH**

---

### 4. Persona Core Token Budget Creates Displacement Risk

**How it works (`src/aijournal/pipelines/persona.py`):**
1. Calculate `effective_strength = strength × exp(-λ × staleness)` for ALL claims
2. Rank by `effective_strength × impact_weight`
3. Select top N claims that fit within ~1200 token budget
4. **Completely overwrite** `derived/persona/persona_core.yaml`

**Problem:**
- A single capture that adds a high-strength claim can **push out** an existing persona claim
- No warning when claims are displaced
- Chat/advice context changes without user awareness

**Risk Level:** 🟠 **HIGH**

---

## Mutation Scope Analysis

### Date-Scoped (Safe)
| Stage | Name | Artifacts | Mutation Type |
|-------|------|-----------|---------------|
| 0 | persist | `data/journal/YYYY/MM/DD/*.md` | Append-only |
| 1 | normalize | `data/normalized/YYYY-MM-DD/*.yaml` | Per-date |
| 2 | summarize | `derived/summaries/<date>.yaml` | Per-date |
| 3 | extract_facts | `derived/microfacts/<date>.yaml` | Per-date |

### Global (High Risk)
| Stage | Name | Artifacts | Risk |
|-------|------|-----------|------|
| 4 | profile_update | `profile/claims.yaml`, `profile/self_profile.yaml` | 🔴 Merges claims, can add/modify/conflict |
| 5 | profile_update | `profile/*.yaml` (via auto-apply) | 🔴 Can downgrade existing claims |
| 6 | index_refresh | `derived/index/*` | 🟡 Additive unless `--rebuild` |
| 7 | persona_refresh | `derived/persona/persona_core.yaml` | 🔴 Complete rebuild, displacement risk |
| 8 | pack | `derived/packs/*.yaml` | 🟢 Read-only snapshot |

---

## Attack Scenarios

### Scenario A: Trivial Entry Pollutes Claims

```bash
aijournal capture --text "Felt good today"  # uses default --apply-profile=auto
```

**LLM generates (from prompt: "default to 0.55 when uncertain"):**
```yaml
claims:
  - type: value
    subject: mood
    predicate: values
    value: "Feeling good is important"
    statement: "User values feeling good"
    strength: 0.55  # speculative
    status: tentative
    method: inferred
```

**Result:**
1. Claim written to `profile/claims.yaml` immediately
2. Persona core rebuilds (stage 7)
3. If token budget tight, this weak claim might displace a stronger one
4. Chat/advice now references this speculative claim

**Likelihood:** 🔴 **HIGH** - Default behavior, no safeguards

---

### Scenario B: Single Entry Contradicts Established Pattern

**Setup:**
- User has 10 entries over 3 months: "I'm a morning person" (strength: 0.82, observations: 10)

**Attack:**
```bash
aijournal capture --text "Stayed up late working, felt super productive at 2am!"
```

**Consolidator behavior (`src/aijournal/services/consolidator.py:189`):**
1. Detects value conflict: "morning person" vs "night owl"
2. **Downgrades existing claim** to `status: tentative`
3. **Reduces strength** of well-established claim
4. Creates interview prompt (but damage already done)

**Result:** Well-established behavioral pattern damaged by single contradictory observation.

**Likelihood:** 🟠 **MEDIUM** - Requires value conflict, but no scope splitting logic

---

### Scenario C: Strength Dilution Over Time

**Setup:**
- User has 100 high-quality entries establishing "values deep work" (strength: 0.85, observations: 100)

**Attack:**
```bash
# User adds 50 mediocre entries with weak signals
for i in {1..50}; do
  aijournal capture --text "Did some work today"  # generates strength ~0.40
done
```

**Math:**
```
Initial: w_prev = min(1.0, log1p(100)) = 1.0, strength = 0.85
After 1st weak entry:  (1.0×0.85 + 1.0×0.40) / 2.0 = 0.625
After 2nd weak entry:  (1.0×0.625 + 1.0×0.40) / 2.0 = 0.513
...
After 50th weak entry: strength ≈ 0.42  ❌ 51% EROSION!
```

**Result:** Claim strength gradually erodes despite having strong historical foundation.

**Likelihood:** 🟠 **MEDIUM** - Natural over time as user adds casual entries

---

### Scenario D: Persona Core Instability

**Every `persona build` (auto-triggered by stage 7):**

```python
# Pseudo-code from persona pipeline
ranked_claims = sorted(claims, key=lambda c: c.effective_strength * impact_weight, reverse=True)
selected = select_until_budget_exhausted(ranked_claims, budget=1200)
write_persona_core(selected)  # COMPLETE OVERWRITE
```

**Problem:**
- A single capture adds claim with strength 0.70
- Existing persona has 24 claims, all strength 0.65-0.75
- New claim displaces lowest-ranked existing claim
- **No warning, no diff, no rollback**

**Impact on downstream:**
- Chat context changes
- Advice recommendations shift
- User doesn't know what changed

**Likelihood:** 🟡 **MEDIUM** - Depends on token budget pressure

---

## Missing Safeguards

### Not Implemented
1. ❌ **Minimum evidence threshold** - Single entry can create claims
2. ❌ **Claim count limits** - No cap on total claims in `profile/claims.yaml`
3. ❌ **Strength floor for new claims** - Accepts strength ≥0.0, prompt defaults to 0.55
4. ❌ **Review diff before apply** - Auto-apply bypasses human inspection entirely
5. ❌ **Rollback mechanism** - No built-in undo for profile changes (must use `git revert`)
6. ❌ **Claim deduplication** - Same concept can spawn multiple similar claims
7. ❌ **LLM confidence validation** - Prompts encourage speculation rather than conservatism
8. ❌ **Persona displacement warnings** - No alert when claims removed from persona core
9. ❌ **Conflict resolution UI** - Downgrades happen silently in consolidator
10. ❌ **Profile backup automation** - No automatic snapshots before mutation

### Weak Safeguards
- ⚠️ **Consolidation weighting** - Logarithmic formula saturates too early (log1p caps at ~2.7)
- ⚠️ **Status field** - LLMs inconsistently use `tentative` vs `accepted`
- ⚠️ **review_after_days** - Doesn't prevent initial pollution, only flags staleness
- ⚠️ **Git version control** - Available but high friction; requires manual `git diff` inspection
- ⚠️ **Scope splitting** - Only handles weekday/weekend, solo/team conflicts; doesn't generalize

---

## Recommendations (Priority Order)

### 🔥 Critical (Do Immediately)

#### 1. Change Default to Review Mode
**File:** `src/aijournal/api/capture.py:26`

```python
# BEFORE:
apply_profile: Literal["auto", "review"] = "auto"

# AFTER:
apply_profile: Literal["auto", "review"] = "review"
```

**Impact:**
- Users must explicitly opt-in to auto-apply with `--apply-profile=auto`
- Forces human review of profile changes by default
- Breaking change but justified for data safety

**Effort:** 🟢 Trivial (1 line)
**Risk:** 🟠 Breaking change for existing users
**Benefit:** 🔴 Prevents silent corruption

---

#### 2. Fix Consolidation Weighting Formula
**File:** `src/aijournal/services/consolidator.py:225`

```python
# BEFORE:
w_prev = min(1.0, math.log1p(n_prev))  # saturates at 1.0 after ~2.7 observations

# OPTION A (Square Root - More Conservative):
w_prev = min(10.0, math.sqrt(n_prev))  # 100 obs → weight 10.0, 10k obs → weight 100.0 capped

# OPTION B (Linear with Cap):
w_prev = min(20.0, n_prev / 5.0)       # 100 obs → weight 20.0

# OPTION C (Higher Log Cap):
w_prev = min(5.0, math.log1p(n_prev))  # 100 obs → weight 4.6, 10k obs → weight 5.0
```

**Recommendation:** Use **Option A** (square root) - balances responsiveness with stability.

**Impact:**
- 100 observations → weight 10.0 vs 1.0 (10x harder to override)
- Single weak entry changes 0.82 → 0.80 instead of 0.82 → 0.56

**Effort:** 🟢 Trivial (1 line)
**Risk:** 🟡 Changes consolidation behavior (regenerate profile recommended)
**Benefit:** 🔴 Major stability improvement

---

#### 3. Add Minimum Evidence Threshold
**File:** `src/aijournal/services/consolidator.py` (new function)

```python
def _has_sufficient_evidence(self, incoming: ClaimAtom, min_sources: int = 2) -> bool:
    """Require at least min_sources evidence spans before accepting new claim."""
    return len(incoming.provenance.sources) >= min_sources

# In _upsert_atoms, before appending new claim:
if index is None:  # new claim
    if not self._has_sufficient_evidence(incoming, min_sources=2):
        return ClaimMergeOutcome(
            changed=False,
            action="insufficient_evidence",
            claim_id=incoming.id or "unknown",
            signature=signature,
        )
    # ... existing code
```

**Impact:**
- Requires at least 2 journal entries before creating a claim
- Prevents single-entry speculation pollution
- Can be configured per-claim-type if needed

**Effort:** 🟡 Moderate (20 lines + tests)
**Risk:** 🟢 Low (only affects new claims)
**Benefit:** 🔴 Major quality improvement

---

#### 4. Strengthen LLM Prompt Conservatism
**Files:** `prompts/profile_update.md`, `prompts/extract_facts.md`

**Find and replace:**
```markdown
# REMOVE:
- "Default to 0.55 when uncertain and note ambiguity in the rationale."

# ADD:
- "Return empty arrays when evidence is weak, ambiguous, or speculative.
   Only propose claims backed by concrete, verifiable observations.
   It is better to return nothing than to add questionable claims.
   Never default to speculative strengths—if uncertain, omit the claim entirely."
```

**Also add to each prompt:**
```markdown
## Quality Standards

Before proposing any claim, ask:
1. Can this be verified by re-reading the journal entries?
2. Would a neutral observer reach the same conclusion?
3. Is there at least 2 independent pieces of supporting evidence?
4. Does the claim reflect a durable pattern rather than a one-off event?

If ANY answer is "no", omit the claim.
```

**Impact:**
- LLMs return fewer, higher-quality claims
- Reduces noise accumulation
- Users get signal, not speculation

**Effort:** 🟢 Easy (prompt edits only)
**Risk:** 🟢 None (only affects new runs)
**Benefit:** 🔴 Major quality improvement

---

### ⚡ High Priority (Do Soon)

#### 5. Add Claim Count Warning
**File:** `src/aijournal/commands/profile.py` (or new `audit` command)

```python
def _check_claim_health(claims: list[ClaimAtom]) -> list[str]:
    warnings = []
    if len(claims) > 100:
        warnings.append(f"Profile has {len(claims)} claims (>100); consider pruning low-strength items.")

    weak_claims = [c for c in claims if c.strength < 0.50]
    if len(weak_claims) > 20:
        warnings.append(f"{len(weak_claims)} claims have strength <0.50; review for quality.")

    return warnings
```

**Integration:** Run in `aijournal status` and `aijournal ops profile status`

**Effort:** 🟢 Easy (15 lines)
**Risk:** 🟢 None (informational only)
**Benefit:** 🟡 Helps users maintain profile quality

---

#### 6. Add Persona Core Diff Display
**File:** `src/aijournal/commands/persona.py`

Before overwriting `persona_core.yaml`:
```python
def _show_persona_diff(old_core: PersonaCore, new_core: PersonaCore) -> None:
    """Display changes in persona core claims."""
    old_ids = {c.id for c in old_core.claims}
    new_ids = {c.id for c in new_core.claims}

    added = new_ids - old_ids
    removed = old_ids - new_ids

    if added:
        typer.echo(f"  Added to persona: {', '.join(added)}")
    if removed:
        typer.secho(f"  ⚠️  Removed from persona: {', '.join(removed)}", fg="yellow")
```

**Effort:** 🟡 Moderate (30 lines)
**Risk:** 🟢 None (display only)
**Benefit:** 🟠 User awareness of changes

---

#### 7. Implement Profile Rollback Command
**File:** `src/aijournal/commands/profile.py` (new command)

```bash
aijournal ops profile rollback --to <commit-hash>
# or
aijournal ops profile rollback --steps 1  # undo last change
```

**Implementation:**
```python
def rollback_profile(workspace: Path, commit: str) -> None:
    """Restore profile/*.yaml from a previous git commit."""
    subprocess.run(["git", "checkout", commit, "--", "profile/"], cwd=workspace, check=True)
    typer.echo(f"Profile restored to {commit}")
    typer.secho("Remember to rebuild persona core: aijournal ops persona build", fg="yellow")
```

**Effort:** 🟡 Moderate (requires git integration)
**Risk:** 🟡 Must validate git state
**Benefit:** 🟠 Safety net for mistakes

---

### 🟡 Medium Priority (Nice to Have)

#### 8. Add Strength Floor for Persona Core
**File:** `src/aijournal/pipelines/persona.py`

```python
MIN_PERSONA_STRENGTH = 0.60

def build_persona_core(...) -> PersonaCore:
    # Filter claims before ranking
    viable_claims = [
        c for c in claims
        if c.effective_strength >= MIN_PERSONA_STRENGTH
    ]
    ranked = sorted(viable_claims, key=ranking_fn, reverse=True)
    # ... rest of logic
```

**Effort:** 🟢 Easy (5 lines)
**Risk:** 🟡 May reduce persona claim count
**Benefit:** 🟡 Improves persona quality

---

#### 9. Implement Semantic Claim Deduplication
**File:** `src/aijournal/services/consolidator.py`

Use embedding similarity or keyword matching to detect duplicate claims before adding:
```python
def _find_semantic_duplicates(claims: list[ClaimAtom], incoming: ClaimAtom) -> list[str]:
    """Find claims with similar subject/predicate/value."""
    # Implementation: cosine similarity on embeddings or simple keyword overlap
    ...
```

**Effort:** 🔴 High (requires embeddings or heuristics)
**Risk:** 🟡 False positives could block valid claims
**Benefit:** 🟡 Reduces noise accumulation

---

#### 10. Add Pre-Apply Validation Hooks
**File:** `src/aijournal/services/capture/stages/stage4_profile.py`

```python
def _validate_proposals(proposals: ProfileUpdateProposals) -> list[str]:
    """Check proposals for quality issues before applying."""
    warnings = []
    for claim_proposal in proposals.claims:
        if claim_proposal.claim.strength < 0.50:
            warnings.append(f"Low strength claim: {claim_proposal.claim.statement}")
        if not claim_proposal.evidence:
            warnings.append(f"No evidence: {claim_proposal.claim.statement}")
    return warnings
```

**Effort:** 🟡 Moderate (validation logic)
**Risk:** 🟢 None (informational)
**Benefit:** 🟡 User awareness

---

## Immediate Action Checklist

For maintainers addressing this risk:

- [ ] **Change default:** Set `apply_profile="review"` in `src/aijournal/api/capture.py:26`
- [ ] **Fix consolidation:** Replace `min(1.0, log1p(n))` with `min(10.0, sqrt(n))` in `src/aijournal/services/consolidator.py:225`
- [ ] **Update prompts:** Remove "default to 0.55" language, add conservative quality standards
- [ ] **Add evidence threshold:** Require ≥2 sources for new claims
- [ ] **Document risk:** Update `ARCHITECTURE.md` Section 3.3 with warning
- [ ] **Update workflow:** Add safety best practices to `docs/workflow.md`
- [ ] **Add tests:** Test consolidation with high-observation claims vs single weak entry
- [ ] **Write migration guide:** Explain breaking change to users (review mode now default)

---

## Testing Checklist

Before deploying fixes:

```bash
# 1. Test consolidation with high-observation claims
pytest tests/test_consolidator.py::test_high_observation_resistance -v

# 2. Test minimum evidence threshold
pytest tests/test_consolidator.py::test_insufficient_evidence_rejection -v

# 3. Test review mode default
pytest tests/test_cli_capture.py::test_capture_review_mode_default -v

# 4. Test persona core stability
pytest tests/test_cli_persona.py::test_persona_displacement_warning -v

# 5. Full regression suite
pytest -xvs
```

---

## Documentation Updates Required

### `ARCHITECTURE.md` Section 3.3 (Consolidation)
Add warning block:

```markdown
⚠️ **Risk Warning:** Claim consolidation uses weighted averaging where existing claims
are weighted by `sqrt(observation_count)` (capped at 10.0). While this provides stability,
a series of weak entries can gradually dilute well-established claims over time.

**Mitigation:** Use `--apply-profile=review` (the default) to inspect changes before applying.
Periodically audit claims with `aijournal ops profile status` and prune low-quality items.
```

### `docs/workflow.md` - Add Safety Section
```markdown
## Safety Best Practices

### Profile Mutation Safety

1. **Review before apply**: The default `--apply-profile=review` mode requires manual inspection
2. **Check diffs**: Run `git diff profile/` before committing profile changes
3. **Backup regularly**: Keep versioned copies of `profile/` outside workspace
4. **Audit claims**: Periodically run `aijournal ops profile status --verbose` to identify:
   - Low-strength claims (strength <0.50)
   - Duplicate or near-duplicate claims
   - Stale claims (not updated in >365 days)
5. **Use rollback**: If profile corrupted, use `git log profile/` + `git checkout <commit> -- profile/`

### When to Use Auto-Apply

Only use `--apply-profile=auto` when:
- Running batch imports of curated notes
- Processing entries you've manually pre-reviewed
- Testing/developing new prompts in isolated workspace

**Never** use auto-apply for:
- Casual daily journaling
- Importing untrusted content
- First-time ingestion of large archives
```

### `README.md` - Add Warning
```markdown
## ⚠️ Important: Profile Safety

By default, `aijournal capture` requires manual review before applying profile changes
(`--apply-profile=review`). This prevents trivial entries from corrupting your self-model.

To auto-apply changes (not recommended for daily use):
```bash
aijournal capture --text "entry" --apply-profile=auto
```

Always inspect profile changes before committing:
```bash
git diff profile/
```
```

---

## Historical Context

**Analysis Date:** 2025-11-01
**Analyzed By:** Automated risk assessment
**Trigger:** User question about trivial capture corruption risk
**Codebase State:** commit `9371746` (after prompt cleanup)

**Key Files Reviewed:**
- `src/aijournal/services/capture/__init__.py` - Pipeline stages
- `src/aijournal/services/consolidator.py` - Claim merging logic
- `src/aijournal/api/capture.py` - Default settings
- `prompts/*.md` - LLM guidance and defaults
- `src/aijournal/pipelines/persona.py` - Persona core generation

**Conclusion:** The system is functional and produces good results under careful use, but lacks
safeguards against accidental profile degradation. The recommended fixes are low-effort and
high-impact, addressing the most critical vulnerabilities without requiring architectural changes.

---

## References

- Consolidation algorithm: `src/aijournal/services/consolidator.py:215-236`
- Capture stages: `src/aijournal/services/capture/__init__.py:47-102`
- Profile apply logic: `src/aijournal/services/capture/stages/stage4_profile.py:53-67`
- Persona rebuild: `src/aijournal/pipelines/persona.py`
- Default settings: `src/aijournal/api/capture.py:26-27`

---

**END OF REPORT**
