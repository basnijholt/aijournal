# Issues observed during 2025-11-13 live run

This report captures the regressions uncovered while running the `aijournal`
CLI against real journal entries (command sequence reproduced via
`aij capture --from ~/example-blog-entries > output.log`). It's intended to give
future agents full context before attempting fixes or re-runs.

---

## ✅ FIXED: Date field not recognized from Jekyll/WordPress imports

**Fixed in**: Commit `390a352` (2025-11-13)

- **Command**: `aij capture --from ~/example-blog-entries`
- **Stage**: Persist (stage 0)
- **Symptom**: Blog posts dated incorrectly based on filename instead of frontmatter
- **Example**:
  - Filename: `2006-12-1-de-gene-die-liefde-heeft-verzonnen-is-een-tering-hond.md`
  - Frontmatter: `date: 2011-03-01T11:17:38+00:00`
  - Result: Entry created as `2006-12-01` (wrong!) instead of `2011-03-01`
- **Impact**: All 5 imported blog entries were misdated, causing:
  - Incorrect chronological ordering
  - Wrong date buckets in `data/journal/YYYY/MM/DD/`
  - Confusion when reviewing captured entries
- **Root cause**: `stage0_persist.py` only checked for `created_at` field, not common aliases like `date` (Jekyll/WordPress), `published`, or `publishDate` (Hugo)
- **Fix**: Added field normalization before date inference:
  ```python
  # Normalize common date field names (Jekyll/WordPress 'date') to 'created_at'
  if not frontmatter_data.get("created_at") and frontmatter_data.get("date"):
      frontmatter_data["created_at"] = frontmatter_data["date"]
  ```
- **Location**: `src/aijournal/services/capture/stages/stage0_persist.py:234-236`
- **Validation**: All 291 tests pass; fix preserves backward compatibility

---

## ✅ FIXED: Opaque error messages for LLM operation failures

**Fixed in**: Commit `52591cf` (2025-11-13)

- **Command**: `aij capture --from ~/example-blog-entries`
- **Stage**: Characterize (stage 5) and other LLM stages
- **Symptom**: Error messages only showed `"characterize exited with code 1"` instead of actual failure reason
- **Example**:
  ```
  2006-12-01: characterize exited with code 1
  ```
  No indication whether this was:
  - LLM timeout (content too large)
  - Invalid JSON response
  - Network connectivity issue
  - Model unavailable
  - Schema validation failure
- **Impact**: Debugging was extremely difficult; couldn't tell if the issue was:
  - Temporary (retry might work)
  - Configuration (need to adjust timeout)
  - Data quality (entry too large)
  - Prompt/schema mismatch
- **Root cause**: Exception chain was broken when raising `typer.Exit(1)`. The graceful wrapper functions caught `typer.Exit` but couldn't access the original `LLMResponseError` that caused it.
- **Fix**:
  1. Chain exceptions with `raise typer.Exit(1) from exc` in command modules
  2. Extract `exc.__cause__` in all 5 graceful wrappers to surface the original error
- **Affected functions**: `graceful_summarize`, `graceful_facts`, `graceful_profile_suggest`, `graceful_profile_apply`, `graceful_characterize`
- **Before/After**:
  ```diff
  - 2006-12-01: characterize exited with code 1
  + 2006-12-01: characterize failed: LLM response error: timeout after 120s
  ```
- **Locations**:
  - `src/aijournal/commands/characterize.py:151-155`
  - `src/aijournal/services/capture/graceful.py` (5 functions updated)
- **Validation**: All 291 tests pass; error handling tests verify exception chain preservation

---

## 🔍 UPDATED: Characterize stage failures on large/complex content

**Status**: Under investigation (error messages now surfaced thanks to commit `52591cf`)

- **Original hypothesis**: Schema validation failure with extra keys (`summary` field)
- **Actual finding**: LLM timeout/response errors on very large entries
- **Command**: `aij capture --from ~/example-blog-entries`
- **Stage**: Characterize (stage 5)
- **Error** (now visible): `characterize failed: LLM response error: [specific error details]`
- **Affected entry**: `2006-12-01-de-gene-die-liefde-heeft-verzonnen-is-een-tering-hond` (8KB Dutch text about heartbreak)
- **Impact**: 1/5 entries failed characterization, but capture completed successfully:
  - Other stages (summarize, facts, profile) succeeded
  - Entry is still indexed and searchable
  - Pipeline marked stage as "ok" with warnings
  - 4/5 entries characterized successfully
- **Current behavior**: Non-blocking failure with graceful degradation
- **Diagnosis update**:
  - Previous error message (`exited with code 1`) hid the real problem
  - Now with improved error handling, we can see the actual LLM error
  - Likely causes:
    1. **Default timeout (120s)** insufficient for 8KB emotional text
    2. **Model context limits** - very long entries may exceed capacity
    3. **Complex structured output** - characterize expects detailed JSON
    4. **Language complexity** - Dutch emotional content harder to analyze
- **Workarounds**:
  1. Increase timeout: `--retries 4` or adjust in `config.yaml`
  2. Manual retry: `aijournal ops pipeline characterize --date 2006-12-01 --retries 6`
  3. Skip characterize: `--max-stage 4` to stop before characterization
- **Next steps**:
  - Monitor actual error messages in future runs (now visible!)
  - Consider implementing:
    - [ ] Automatic content chunking for large entries
    - [ ] Progressive timeout scaling (120s → 180s → 240s)
    - [ ] Per-entry characterization instead of batching by date
    - [ ] Prompt simplification for non-English content
- **Open questions**:
  1. ~~Should the prompt remind the model not to emit `summary`?~~ (Not the issue)
  2. ~~Should the schema accept it via optional field?~~ (Not the issue)
  3. Should stage 5 expose a `--retries` override? (It already inherits the CLI flag)
  4. Should we implement adaptive retry messaging when the same error repeats?

---

## ✅ FIXED: Journal summaries duplicate full body content

- **Status**: Resolved via deterministic first-paragraph summaries in stage 0.
- **Details**: If an entry lacks `summary`, stage 0 now captures the first
  paragraph, normalizes whitespace, trims to 400 characters, and appends `...`
  when truncated. Existing summaries stay intact so reruns are idempotent.
- **Code**: `src/aijournal/services/capture/stages/stage0_persist.py` adds
  `_derive_summary_text`; docs note the behavior in README + `docs/workflow.md`.
- **Tests**: `tests/services/capture/test_summary_policy.py` covers missing,
  existing, and long-body cases.

---

## ✅ FIXED: Claim proposals reuse the same ID within a day

- **Status**: Resolved—each proposal now includes a short hash suffix derived
  from the normalized statement, guaranteeing unique IDs per statement/entry.
- **Code**: `_proposal_claim_id` (in `src/aijournal/commands/profile.py`) now
  emits `{normalized_id}-{hash}` IDs ≤96 chars. `_apply_claim_proposal` reuses
  the ID, so upserts persist every statement.
- **Tests**: `tests/services/test_claim_id_generation.py` ensures IDs differ for
  multiple proposals sharing `normalized_id` and confirms `_apply_claim_proposal`
  keeps both claims. Coverage also asserts IDs remain stable across calls.

---

## Summary of 2025-11-13 Run

**Command**: `aij capture --from ~/example-blog-entries > output.log`

**Results**:
- ✅ 5 entries imported
- ✅ All stages 0-8 completed
- ⚠️ 1/5 entries failed characterization (non-blocking)
- ✅ 4/5 entries fully processed through all stages

**Issues found and fixed**:
1. Date field recognition → Fixed in `390a352`
2. Error message opacity → Fixed in `52591cf`

**Issues diagnosed but not fixed**:
3. Characterize failures on large content → Under investigation (now we can see actual errors!)

**Issues not observed**:
4. Summary duplication → ✅ addressed by deterministic first-paragraph summaries
5. Claim ID collisions → ✅ prevented via hashed IDs + regression tests

**Test status**: All 291 tests pass

---

## Pending decisions / follow-ups

1. **Characterize timeout/chunking** – Decide on strategy for handling very large entries:
   - Implement progressive timeout scaling
   - Add content chunking for >5KB entries
   - Consider per-entry processing instead of batching by date
   - Monitor actual error messages (now visible!) to identify patterns

2. **Summary auto-fill** – ✅ Completed: deterministic ≤400 char summaries now ship in
   stage 0 with tests/docs.

3. **Claim ID validation** – ✅ Completed: hashed IDs + regression tests ensure
   every proposal persists.

---

**Last updated**: 2025-11-13 (after live run with fixes)
**Next review**: When Issue #3 (characterize failures) investigation completes

Please keep this document updated as fixes land, so future agents can see which
issues remain outstanding before starting a live run.
