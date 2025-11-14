# aijournal TODO

Active tasks and planned improvements for the aijournal project.

---

## 1. Automatic Resume / Completion Checker for Capture

**Priority:** High
**Status:** Planned
**Source:** plan.md §2

**Pain Point:** When an LLM stage fails during capture, recovery is manual. Operators must inspect `derived/logs/capture/<run_id>.jsonl` and rerun individual `ops` commands. There's no high-level view of "what still needs to be done."

**Proposal:** Provide a CLI helper that auto-detects incomplete derivations and optionally replays them.

**Implementation:**

1. **Detection mode** – Inspect filesystem state to compute which stages are missing for each date:
   - Normalized entry exists but `derived/microfacts/<date>.yaml` missing or older than source hash
   - Pending batches left unapplied
   - Persona/index stale
   - Output a checklist grouped by date/stage

2. **Replay mode** – Execute missing stages in dependency order (scoped rerun of capture limited to detected gaps)

**Acceptance Criteria:**
- New command: `aijournal ops pipeline resume --date YYYY-MM-DD` or `--run capture-YYYYMMDDHHMMSS`
- Supports `--detect-only` (prints pending steps) or `--apply` (executes them)
- Detection relies on artifact mtimes/hashes—no new persistent state files
- Replay mode reuses existing command runners (no duplicated business logic)
- Records results back into capture telemetry

**Implementation Notes:**
- Build helpers to compare `data/normalized/`, `derived/summaries/`, `derived/microfacts/`, etc.
- Use stored `source_hash` / `manifest_hashes` when available
- Update docs/workflow to mention the resume command under troubleshooting

---

## 2. Structured Failure Reporting

**Priority:** Medium
**Status:** Planned (blocked by §1)
**Source:** plan.md §3

**Goal:** Surface failed stages in telemetry and status command to guide operators toward recovery actions.

**Proposal:**
- Extend `capture-<id>.result.json` with a `failed_stages` array containing stage name, date, and error message
- Surface recent failed stages in `aijournal status`, nudging operators to run the resume helper
- Enable downstream tooling (CLI, UI, status command) to quickly surface "profile update failed for 2025-10-28" without parsing NDJSON

**Acceptance Criteria:**
- `capture-<id>.result.json` includes `failed_stages: [{stage: str, date: str, error: str}]`
- `aijournal status` displays recent failures with actionable suggestions
- Resume helper (§1) consumes this data for detection

---

## 3. Validation & Evaluation Toolkit

**Priority:** Medium
**Status:** Research phase
**Source:** REVIEW.md §7-8

### 3.1 Survey/EMA Ingestion & Reporting

**Goal:** Track convergent/discriminant/test-retest/calibration stats and enforce kill criteria for scientific credibility.

**Components:**

1. **Data Ingestion:**
   - Store survey and EMA results as artifacts: `derived/evaluations/ema/YYYY-MM-DD.yaml`
   - Support standard personality inventories (BFI-2, IPIP, Schwartz values)
   - Capture Experience Sampling Method (EMA) data (1-3 items/day)

2. **Metrics Commands:**
   - `aijournal ops persona calibrate` – Import survey/EMA data
   - `aijournal ops persona metrics` – Compute validation statistics

3. **Validation Metrics:**
   - **Convergent validity:** Correlate grouped claim strengths with survey scores (target `r ≥ .30`)
   - **Discriminant validity:** Keep cross-trait correlations `|r| < .25`
   - **Test-retest reliability:** Compute ICCs over 4-6 weeks (expect `≥ 0.60`)
   - **State density integration:** Align L1 strengths with EMA means/variances
   - **Predictive validity:** Track baseline vs. post-advice success
   - **Calibration:** Brier scores & reliability curves for binary claims
   - **Inter-rater reliability:** Cohen's κ / Jaccard on claim signatures

4. **Kill Criteria:**
   - Convergent validity `< .20`
   - Advice yields no improvement
   - Calibration remains poor after 8-12 weeks per user

**Acceptance Criteria:**
- Commands exist and run without errors
- Metrics are stored as artifacts with ArtifactMeta envelopes
- Documentation explains how to interpret each metric
- Tests cover happy path and edge cases

### 3.2 Extend AdviceCard with Mechanistic Tags

**Goal:** Ground advice in behavior change theory and ensure traceability to evidence.

**Requirements:**

1. **COM-B Framework:** Add `com_b_lever` field to `AdviceCard`
   - Values: `capability`, `opportunity`, `motivation`, `null`
   - Tags which behavior change mechanism each recommendation targets

2. **Implementation Intentions:** Add `if_then` field
   - Format: `{if: str, then: str}` for concrete "if-then" plans
   - Optional field, null when not applicable

3. **Evidence Citations:**
   - Each recommendation must cite `≥1` claim ID
   - Each recommendation must cite `≥1` recent evidence ID
   - Validate citations during card generation

**Acceptance Criteria:**
- `AdviceCard` schema updated with new fields
- Prompt template instructs LLM to populate these fields
- Validation enforces citation requirements
- Tests cover all combinations of tags/citations

---

## 4. Trusted-Other Ingestion (Optional)

**Priority:** Low
**Status:** Research phase
**Source:** REVIEW.md §8

**Goal:** Address self-other knowledge asymmetry by incorporating informant perspectives.

**Proposal:**
- Provide `aijournal capture --trusted-other` for informant input
- Store as separate evidence channels with independent decay
- Tag claims with `method: informant` to distinguish from self-report
- Maintain separate provenance chains

**Research Questions:**
- How to weight informant data vs. self-report?
- How to handle conflicting observations?
- Privacy/consent considerations for storing others' input

**Acceptance Criteria:**
- Flag exists and creates separate evidence artifacts
- Consolidator handles mixed evidence sources correctly
- Documentation explains use cases and privacy considerations
- Tests cover informant + self-report scenarios

---

## Implementation Guidelines

### Commit Discipline

**Critical:** Each TODO item must be completed in its own commit (or sequence of commits), and the test suite **must** be green before and after every commit. No exceptions.

### Testing Requirements

- Run `uv run pytest -q` before committing
- All 215+ tests must pass
- Add new tests for new functionality
- Update existing tests when behavior changes

### Documentation Requirements

- Update `README.md` for user-facing changes
- Update `ARCHITECTURE.md` for system design changes
- Update `docs/workflow.md` for new commands/workflows
- Keep examples in sync with code

### Code Quality

- Run `uv run pre-commit run --all-files` before committing
- Fix all ruff, ruff-format, and mypy issues
- Maintain StrictModel discipline (no extra fields)
- Preserve artifact envelopes and provenance redaction

---

## Historical Context

See `docs/archive/REVIEW.md` and `docs/archive/plan.md` for completed work and detailed technical context.
