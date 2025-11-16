# Profile Update Retirement Checklist

_Updated: 2025-11-14_

This document provides a file-by-file retirement plan for fully deprecating the legacy `profile_suggest` (stage 4) and `characterize` (stage 5) flows once the unified `profile_update` capture stage is validated and stable.

> Status: The checklist below is now historical—every item has been executed as of 2025-11-14. Legacy names remain in this file for audit purposes only.

---

## Overview

**Goal**: Replace the two-stage profile update workflow (`profile_suggest` → `characterize`) with a single unified `profile_update` stage that uses `prompts/profile_update.md` and produces identical `ProfileUpdateProposals` artifacts.

**Migration Path**:
1. Validate unified `profile_update` stage produces equivalent outputs
2. Update capture pipeline to use new stage
3. Remove legacy commands, prompts, and tests
4. Update documentation and migration guides

---

## Behavioral Invariants (Must Remain True)

These behaviors must be preserved after the cleanup:

### Core Workflow Guarantees
- ✅ `capture` command automatically derives profile updates for changed dates
- ✅ Profile update batches land in `derived/pending/profile_updates/` with manifest hashes
- ✅ `--apply-profile=auto` applies batches immediately during capture
- ✅ Manual review via `ops pipeline review --file <batch> --apply` still works
- ✅ Applied batches move to `derived/pending/profile_updates/applied/`
- ✅ Interview prompts are preserved in batch outputs

### Artifact Format Compatibility
- ✅ Batch YAML schema remains `ProfileUpdateProposals` (claims, facets, interview_prompts)
- ✅ Claim proposals include `id`, `statement`, `strength`, `reason`, `method`, `review_cadence`, `sources`
- ✅ Facet changes use `set`/`remove` operations on persona fields
- ✅ Manifest hash tracking prevents duplicate processing

### Integration Points
- ✅ `ops profile apply` command accepts both date-based and file-based inputs
- ✅ `ops profile status` shows pending batches and review priorities
- ✅ Chat/advice surfaces reflect applied profile updates
- ✅ Persona rebuild incorporates new claims/facets
- ✅ Index refresh includes new evidence spans

### Test Coverage
- ✅ All existing capture integration tests pass
- ✅ Profile apply/status workflows validated
- ✅ Batch serialization/deserialization round-trips correctly
- ✅ Simulator validators confirm stage outputs

---

## Files to Remove

### 1. Legacy Prompts
**Files**:
- `prompts/profile_suggest.md`
- `prompts/characterize.md`
- `prompts/examples/profile_suggest.json`
- `prompts/examples/characterize.json`

**Validation**:
- Verify `prompts/profile_update.md` and `prompts/examples/profile_update.json` exist
- Confirm no other code references these prompt files
- Update prompt hash validation if needed

**Commands**:
```bash
rm prompts/profile_suggest.md
rm prompts/characterize.md
rm prompts/examples/profile_suggest.json
rm prompts/examples/characterize.json
```

---

### 2. Legacy Pipeline Modules
**Files**:
- `src/aijournal/pipelines/characterize.py`

**Note**: Keep normalization helpers if still used by unified pipeline

**Validation**:
- Ensure `src/aijournal/pipelines/profile_update.py` provides all needed functionality
- Check if any normalization helpers are still referenced
- Verify `fake_profile_proposals()` covers both legacy fake modes

**Migration Steps**:
1. Move any shared normalization logic to `profile_update.py`
2. Update imports in tests that reference `characterize_pipeline`
3. Remove the file

**Commands**:
```bash
# After confirming no dependencies remain:
rm src/aijournal/pipelines/characterize.py
```

---

### 3. Legacy Command Modules
**Files**:
- `src/aijournal/commands/characterize.py`
- `src/aijournal/commands/profile.py` (partial - only `run_profile_suggest`)

**Validation**:
- Confirm `src/aijournal/commands/profile_update.py` exists and is wired
- Verify `run_profile_apply` and `run_profile_status` are preserved
- Check CLI wiring in `cli.py` has been updated

**Migration Steps**:
1. Extract `run_profile_apply`, `run_profile_status`, and shared helpers from `profile.py`
2. Create new home for these functions (e.g., `commands/profile_apply.py` or consolidate into `profile_update.py`)
3. Remove `run_profile_suggest` and characterize-specific code
4. Delete `characterize.py` entirely

**Commands**:
```bash
# After extracting needed functions:
rm src/aijournal/commands/characterize.py
# Edit profile.py to remove run_profile_suggest or delete if fully replaced
```

---

### 4. Legacy Capture Stages
**Files**:
- `src/aijournal/services/capture/stages/stage4_profile.py`
- `src/aijournal/services/capture/stages/stage5_characterize.py`

**Validation**:
- Confirm unified `stage4_profile_update.py` (or equivalent) exists
- Verify new stage produces `ProfileUpdateStageOutputs` (replacing both old outputs)
- Check stage registry in `__init__.py` has been updated

**Migration Steps**:
1. Create new unified stage module (e.g., `stage4_profile_update.py`)
2. Update `CAPTURE_STAGES` list in `services/capture/__init__.py`
3. Update stage execution logic in `run_capture()`
4. Remove old stage modules

**Commands**:
```bash
# After unified stage is wired:
rm src/aijournal/services/capture/stages/stage4_profile.py
rm src/aijournal/services/capture/stages/stage5_characterize.py
```

---

### 5. Legacy Graceful Wrappers
**Files**:
- `src/aijournal/services/capture/graceful.py` (partial)

**Functions to Remove**:
- `graceful_profile_suggest()`
- `graceful_characterize()`

**Functions to Keep**:
- `graceful_profile_apply()`
- `graceful_summarize()`
- `graceful_facts()`
- Any other graceful wrappers

**Validation**:
- Confirm new `graceful_profile_update()` exists
- Verify no tests or stages call old wrappers

**Migration Steps**:
1. Add `graceful_profile_update()` wrapper
2. Update stage imports to use new wrapper
3. Remove old functions from `graceful.py`

---

### 6. Legacy CLI Commands
**Files**:
- `src/aijournal/cli.py` (partial)

**Commands to Remove**:
- `@profile_app.command("suggest")` → `profile_suggest()`
- `@ops_pipeline_app.command("characterize")` → `characterize()`

**Commands to Keep**:
- `@profile_app.command("update")` → `profile_update_cli()` ✅ Already exists
- `@profile_app.command("apply")` → `profile_apply()`
- `@profile_app.command("status")` → `profile_status()`
- `@ops_pipeline_app.command("review")` → `review_updates()`

**Validation**:
- Verify `ops profile update` command is wired and functional
- Confirm help text and examples reference new unified command
- Update any command aliases or deprecation warnings

**Migration Steps**:
1. Add deprecation warnings to old commands (optional grace period)
2. Update CLI help text and examples
3. Remove old command decorators and functions
4. Update CLI tests

**Commands**:
```bash
# Edit cli.py to remove:
# - profile_suggest() function and @profile_app.command("suggest")
# - characterize() function and @ops_pipeline_app.command("characterize")
```

---

### 7. Legacy Output Types
**Files**:
- `src/aijournal/services/capture/__init__.py` (partial)

**Types to Remove**:
- `ProfileStage4Outputs` (NamedTuple with suggest_result, apply_result, etc.)
- `CharacterizeStage5Outputs` (NamedTuple with result, review_result, etc.)

**Types to Add**:
- `ProfileUpdateStageOutputs` (unified replacement)

**Validation**:
- Confirm new output type captures all needed fields
- Verify stage result reporting still works
- Check simulator validators updated

**Migration Steps**:
1. Define new `ProfileUpdateStageOutputs` NamedTuple
2. Update stage function signatures
3. Update result aggregation in `run_capture()`
4. Remove old NamedTuples

---

### 8. Legacy Tests
**Files**:
- `tests/test_cli_profile_suggest.py` (entire file)
- `tests/test_cli_characterize.py` (entire file)
- `tests/pipelines/test_characterize.py` (entire file)
- `tests/services/capture/test_stage_characterize.py` (entire file)
- `tests/services/capture/test_stage_profile.py` (partial - stage 4 tests only)

**Tests to Preserve**:
- Profile apply/status tests
- Capture integration tests (update to new stage)
- Batch serialization tests
- Profile update proposal validation

**Validation**:
- Confirm new `tests/commands/test_profile_update.py` covers equivalent scenarios
- Verify `tests/services/capture/test_stage_profile_update.py` exists
- Check integration tests updated in `tests/services/test_capture.py`

**Migration Steps**:
1. Create new test files for unified pipeline
2. Port critical test cases to new structure
3. Update integration tests to use new stage
4. Remove legacy test files

**Commands**:
```bash
rm tests/test_cli_profile_suggest.py
rm tests/test_cli_characterize.py
rm tests/pipelines/test_characterize.py
rm tests/services/capture/test_stage_characterize.py
# Edit test_stage_profile.py to remove stage 4 tests
```

---

### 9. Legacy Prompt DTO Definitions
**Files**:
- `src/aijournal/domain/prompts.py` (partial)

**DTOs to Remove**:
- `PromptProfileUpdates` (if superseded by unified schema)
- `PromptClaimItem` (if no longer used)
- Legacy converters specific to old prompts

**DTOs to Keep**:
- `ProfileUpdateProposals` (target format)
- `ClaimProposal`, `FacetChange` (used by unified system)
- Any shared converters

**Validation**:
- Confirm unified prompt uses same output schema
- Verify no serialization/deserialization breaks
- Check backward compatibility if old batches exist

**Migration Steps**:
1. Audit which DTOs are still referenced
2. Remove unused legacy schemas
3. Update type hints and imports

---

### 10. Legacy Fake Mode Implementations
**Files**:
- `src/aijournal/fakes.py` (partial)

**Functions to Audit**:
- `fake_profile_suggest()` - likely superseded
- `fake_characterize()` - likely superseded
- `fake_profile_proposals()` - should cover both legacy modes

**Validation**:
- Confirm `fake_profile_proposals()` returns `ProfileUpdateProposals`
- Verify fake mode tests pass with unified pipeline
- Check simulator uses correct fake functions

**Migration Steps**:
1. Consolidate fake implementations if needed
2. Remove unused fake functions
3. Update tests to use unified fake mode

---

### 11. Documentation Updates
**Files to Update**:
- `README.md` - Update command examples
- `docs/workflow.md` - Replace two-stage flow with unified stage
- `ARCHITECTURE.md` - Update stage descriptions
- `CLAUDE.md` - Update operator guide with new commands
- `CHANGELOG.md` - Document breaking change

**Key Changes**:
- Replace `ops profile suggest` with `ops profile update`
- Remove `ops pipeline characterize` references
- Update capture stage count (now 8 stages instead of 9)
- Clarify stage 4 is now unified profile_update
- Remove stage 5 characterize_review references

**Example Updates**:

**Before** (docs/workflow.md):
```markdown
4. Profile suggest (stage 4): `uv run aijournal ops profile suggest --date YYYY-MM-DD`
5. Profile apply: `uv run aijournal ops profile apply --date YYYY-MM-DD --yes`
6. Characterize (stage 5): `uv run aijournal ops pipeline characterize --date YYYY-MM-DD`
7. Review batches: `uv run aijournal ops pipeline review --file <batch>.yaml --apply`
```

**After**:
```markdown
4. Profile update (stage 4): `uv run aijournal ops profile update --date YYYY-MM-DD`
5. Profile apply: `uv run aijournal ops profile apply --date YYYY-MM-DD --yes`
   (Or use `--apply-profile=auto` in capture to skip manual apply)
```

---

### 12. Simulator Validators
**Files**:
- `src/aijournal/simulator/validators.py` (partial)

**Updates Needed**:
- Replace stage name `"derive.characterize"` with `"derive.profile_update"`
- Update stage 5 references to point to new unified stage
- Verify batch validation logic works with unified outputs
- Update `CAPTURE_STAGE_NAMES` constant

**Validation**:
- Run full simulator suite after changes
- Confirm stage completion tracking works
- Verify batch apply validation passes

---

### 13. Capture Stage Registry
**Files**:
- `src/aijournal/services/capture/__init__.py`

**Updates Needed**:

**Before** (CAPTURE_STAGES list):
```python
CaptureStage(4, "profile_update", "Generate profile suggestions and optionally apply them.", "..."),
CaptureStage(5, "characterize_review", "Characterize entries and review new batches...", "..."),
```

**After**:
```python
CaptureStage(4, "profile_update", "Derive profile updates (claims, facets, interview prompts) and optionally apply.", "uv run aijournal ops profile update --date YYYY-MM-DD\nuv run aijournal ops profile apply --date YYYY-MM-DD --yes"),
# Stage 5 removed - unified into stage 4
CaptureStage(5, "index_refresh", "Refresh the retrieval index...", "..."),  # Renumber remaining stages
```

**Migration Steps**:
1. Update stage definitions
2. Renumber stages 6-8 to 5-7
3. Update imports and stage execution logic
4. Update stage name constants in validators

---

## Migration and Compatibility Steps

### Phase 1: Validation (Pre-Removal)
1. **Parallel Validation**:
   - Run both old and new pipelines on same inputs
   - Compare `ProfileUpdateProposals` outputs for equivalence
   - Verify batch apply produces identical profile changes
   - Check interview prompts are preserved

2. **Integration Testing**:
   ```bash
   # Run full capture with new unified stage
   uv run aijournal capture --from tests/fixtures/journal --apply-profile=auto

   # Verify outputs match legacy format
   uv run aijournal ops profile status
   uv run aijournal ops pipeline review --dry-run
   ```

3. **Simulator Validation**:
   ```bash
   # Run full simulator suite
   uv run pytest tests/simulator/ -v

   # Verify all stage validators pass
   ```

### Phase 2: Deprecation Warnings (Optional Grace Period)
1. Add deprecation warnings to legacy commands:
   ```python
   @profile_app.command("suggest", deprecated=True)
   def profile_suggest(...):
       console.print("[yellow]Warning: 'profile suggest' is deprecated. Use 'profile update' instead.[/yellow]")
       # ... existing implementation
   ```

2. Update help text to point to new commands

3. Log deprecation telemetry for monitoring

### Phase 3: Removal (After Validation)
1. **Remove Files** (per checklist above)
2. **Update Tests**:
   ```bash
   uv run pytest  # Should pass with ~same coverage
   ```
3. **Update Docs**:
   ```bash
   # Verify README examples work
   grep -r "profile suggest" docs/  # Should find no matches
   grep -r "characterize" docs/ | grep -v "profile_update"  # Should be clean
   ```

4. **Commit**:
   ```bash
   git add -A
   git commit -m "Retire legacy profile_suggest and characterize flows

   - Remove prompts/profile_suggest.md and prompts/characterize.md
   - Delete legacy pipeline and command modules
   - Consolidate capture stages 4+5 into unified profile_update stage
   - Update all documentation and tests
   - Preserve profile apply/status/review workflows"
   ```

### Phase 4: Post-Removal Validation
1. **Full Rehearsal** (per CLAUDE.md):
   ```bash
   # Run complete live workflow
   export RUN_ROOT=/tmp/aijournal_retirement_validation
   uv run aijournal --path "$RUN_ROOT" init
   cd "$RUN_ROOT"

   # Capture entries and verify pipeline
   uv run aijournal capture --from ~/notes --apply-profile=auto
   uv run aijournal status
   uv run aijournal chat "What progress did I make?"
   ```

2. **Test Coverage Verification**:
   ```bash
   uv run pytest --cov=src/aijournal --cov-report=term-missing
   # Ensure coverage remains ≥85%
   ```

3. **Documentation Review**:
   - Verify all command examples work
   - Check help text is accurate
   - Confirm CLAUDE.md operator guide is current

---

## Rollback Plan

If issues are discovered after removal:

### Immediate Rollback
1. Revert the retirement commit:
   ```bash
   git revert <retirement-commit-sha>
   ```

2. Restore legacy files from git history:
   ```bash
   git checkout <pre-retirement-commit> -- prompts/profile_suggest.md
   git checkout <pre-retirement-commit> -- src/aijournal/commands/characterize.py
   # ... restore other needed files
   ```

### Partial Rollback
If only specific functionality is broken:
1. Keep unified pipeline as default
2. Re-add legacy commands as hidden fallbacks
3. Add compatibility shim in capture orchestrator
4. File detailed bug report with reproduction steps

---

## Success Criteria

The retirement is complete and successful when:

- ✅ All tests pass (`uv run pytest`)
- ✅ Pre-commit hooks pass (`pre-commit run --all-files`)
- ✅ Full rehearsal completes 350/350 score per CLAUDE.md
- ✅ No references to `profile_suggest` or `characterize` in docs (except this file)
- ✅ CLI help text shows only `ops profile update`
- ✅ Capture stage count is correct (8 stages, numbered 0-7)
- ✅ Simulator validators updated and passing
- ✅ CHANGELOG.md documents the breaking change
- ✅ Migration guide added for operators with existing batches

---

## Appendix: Quick Checklist

**Before Starting**:
- [ ] Unified `profile_update` stage validated and stable
- [ ] Output parity confirmed with legacy flows
- [ ] Integration tests pass with new pipeline
- [ ] Full rehearsal completes successfully

**Removal Tasks**:
- [ ] Remove `prompts/profile_suggest.md` and `prompts/characterize.md`
- [ ] Remove `prompts/examples/profile_suggest.json` and `characterize.json`
- [ ] Remove `src/aijournal/pipelines/characterize.py`
- [ ] Remove `src/aijournal/commands/characterize.py`
- [ ] Extract survivors from `src/aijournal/commands/profile.py` and remove legacy code
- [ ] Remove `src/aijournal/services/capture/stages/stage4_profile.py`
- [ ] Remove `src/aijournal/services/capture/stages/stage5_characterize.py`
- [ ] Remove `graceful_profile_suggest` and `graceful_characterize` from `graceful.py`
- [ ] Remove `profile_suggest` and `characterize` CLI commands from `cli.py`
- [ ] Remove `ProfileStage4Outputs` and `CharacterizeStage5Outputs` from capture `__init__.py`
- [ ] Remove legacy test files
- [ ] Clean up unused DTOs in `domain/prompts.py`
- [ ] Consolidate fake implementations in `fakes.py`
- [ ] Update `CAPTURE_STAGES` registry and renumber stages
- [ ] Update simulator validators

**Documentation Updates**:
- [ ] Update README.md command examples
- [ ] Update docs/workflow.md to show unified flow
- [ ] Update ARCHITECTURE.md stage descriptions
- [ ] Update CLAUDE.md operator guide
- [ ] Add CHANGELOG.md entry for breaking change

**Validation**:
- [ ] Run `uv run pytest` - all tests pass
- [ ] Run `pre-commit run --all-files` - no issues
- [ ] Complete full rehearsal per CLAUDE.md - 350/350 score
- [ ] Verify no legacy references remain: `grep -r "profile_suggest\|characterize" docs/ src/`

**Post-Removal**:
- [ ] Commit with detailed message
- [ ] Create PR with retirement summary
- [ ] Update any external documentation or guides
- [ ] Archive this checklist for future reference

---

_End of retirement checklist._
