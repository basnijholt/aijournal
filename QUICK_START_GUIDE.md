# Quick Start: Test Coverage Audit Summary

**Time to read**: 5 minutes | **Status**: Ready to implement

---

## The Situation

aijournal has **215 tests** but **6 critical coverage gaps**:

| Gap | Status | Risk |
|-----|--------|------|
| 7 of 9 capture stages have zero tests | ❌ Missing | Critical |
| No full-pipeline E2E tests | ❌ Missing | Critical |
| Only 1 fixture (need 8+) | ❌ Missing | High |
| All tests use fake LLM | ❌ Zero validation | High |
| Only 5 error-handling tests | ❌ 67% gap | High |
| No multi-day workflow tests | ❌ Missing | Medium |

---

## The Solution: Simulator Harness

A **deterministic test framework** with 5 reusable modules:

```
tests/simulator/
├── fixtures.py      → Build test workspaces
├── harness.py       → Run stages sequentially
├── validators.py    → Check stage output
├── reporters.py     → Clear error messages
└── seeds.py         → Deterministic data generators

tests/integration/
├── test_stage_0_persist.py      (4 tests)
├── test_stage_1_normalize.py    (3 tests)
├── test_stage_3_facts.py        (3 tests)
├── ...other stages...
└── test_full_pipeline.py        (5+ tests)
```

---

## Quick Example

### Before (Mocked)
```python
def test_summarize_fake_mode():
    """Test summarize with fake LLM."""
    entries = [_normalized_entry("entry-1")]
    result = summarize.generate_summary(
        entries,
        "2024-01-02",
        use_fake_llm=True,  # ← Mocked, won't catch real issues
        ...
    )
    assert result.day == "2024-01-02"
```

### After (Integration)
```python
def test_full_pipeline_single_entry(tmp_path: Path):
    """E2E: capture → normalize → summarize → facts → profile → pack."""

    # Setup
    fixture = (WorkspaceFixture(tmp_path, seed=42)
        .with_normalized_entry(
            date="2025-10-20",
            entry_id="2025-10-20-deep-work",
            summary="Focused on architecture",
            tags=["focus"],
        )
        .with_manifest_entry(
            entry_id="2025-10-20-deep-work",
            source_hash="abc123",
            path="data/journal/2025/10/20/deep-work.md",
        )
        .build())

    # Run stages 0-8
    orchestrator = StageOrchestrator(fixture)
    result = orchestrator.run_through(max_stage=8, date="2025-10-20")

    # Verify
    assert result.success, result.errors
    assert all(s.success for s in result.stages)  # Each stage passed
```

---

## Implementation Plan

### Phase 1: Foundation (20 hours) ← START HERE
- Create 5 simulator modules
- Write 35 stage tests (all 9 stages covered)
- Create 8 fixture templates
- **Output**: 50+ new tests, closes critical gaps

### Phase 2: Error Handling (8 hours)
- 10-12 error scenario tests
- Additional fixtures
- **Output**: 15-20 error tests

### Phase 3: Live LLM (2 hours, optional)
- Real Ollama validation
- **Output**: 5+ live-mode tests

---

## Key Design Principles

### 1. Determinism
- All tests use fixed seeds: `seed=42`
- All timestamps monkeypatched: `_FIXED_NOW = datetime(2025, 2, 3, ...)`
- Zero flaky tests, 100% reproducible

### 2. Stage Validation
Each stage has dedicated validator:
```python
errors = StageValidator.validate_stage_2(workspace, date="2025-10-20")
if errors:
    print(f"Summary validation failed: {errors}")
```

### 3. Clear Reporting
```
╔════════════════════════════════════════╗
║ STAGE 2 (Summarize) VALIDATION FAILED  ║
╚════════════════════════════════════════╝

Errors:
1. Missing field 'bullets' in summary artifact
2. Field 'highlights' is empty list

Expected at:
/tmp/test/derived/summaries/2025-10-20.yaml

Next steps:
1. Check summarize service logs
2. Run manually: aijournal ops pipeline summarize ...
```

### 4. Reusability
Fixtures compose with fluent API:
```python
fixture = (WorkspaceFixture(tmp_path, seed=42)
    .with_normalized_entry(...)
    .with_manifest_entry(...)
    .with_daily_summary(...)
    .with_claims(...)
    .build())
```

---

## Expected Impact

**Coverage Before**:
- Unit: 80 ✅
- Component: 100 ✅
- Integration: 35 ⚠️
- E2E: 0 ❌
- Error: 5 ❌
- Stages: 2/9 ❌

**Coverage After Phase 1**:
- Unit: 80 ✅
- Component: 100 ✅
- Integration: 50-60 ✅
- E2E: 5+ ✅
- Error: 15-20 ✅
- Stages: 9/9 ✅

**Net Result**: +50 tests, ~90% integration coverage, zero critical gaps

---

## Files to Read

| File | Size | Purpose | Read Time |
|------|------|---------|-----------|
| IMPLEMENTATION_SUMMARY.md | 8 KB | Overview & recommendations | 10 min |
| AUDIT_TEST_COVERAGE.md | 22 KB | Detailed gap analysis | 20 min |
| SIMULATOR_HARNESS_DESIGN.md | 25 KB | Implementation guide | 30 min |
| QUICK_START_GUIDE.md | 3 KB | This file | 5 min |

**Total**: ~60 KB documentation, comprehensive

---

## How to Start

### Week 1: Planning & Setup
1. Read IMPLEMENTATION_SUMMARY.md (10 min)
2. Read SIMULATOR_HARNESS_DESIGN.md (30 min)
3. Create branch: `feature/simulator-harness-foundation`
4. Create file structure:
   ```bash
   mkdir -p tests/simulator
   mkdir -p tests/integration
   touch tests/simulator/__init__.py
   touch tests/simulator/fixtures.py
   touch tests/simulator/harness.py
   # ... etc
   ```

### Week 1-2: Core Implementation
1. Implement `tests/simulator/fixtures.py` → `WorkspaceFixture` class
2. Implement `tests/simulator/harness.py` → `StageOrchestrator` class
3. Implement `tests/simulator/validators.py` → Stage validators
4. Implement `tests/simulator/reporters.py` → Error reporters
5. Implement `tests/simulator/seeds.py` → Data generators

### Week 2-3: Tests
1. Write stage 0 tests (4 tests)
2. Write stage 1 tests (3 tests)
3. Write stage 3-6 tests (9 tests)
4. Write stage 8 + full pipeline tests (6+ tests)
5. Run full suite: `pytest tests/integration/ -v`

### Week 3-4: Polish & Error Handling
1. Add error scenario tests (10-12)
2. Refine fixture library (add missing fixtures)
3. Improve error messages
4. Update documentation

---

## Success Metrics

- ✅ All 9 stages have dedicated tests
- ✅ Full pipeline validates end-to-end
- ✅ 50-60 new integration tests
- ✅ ~90% integration coverage
- ✅ Clear, actionable failure messages
- ✅ Zero flaky tests
- ✅ Fixtures are reusable
- ✅ Code is mergeable in phases

---

## Common Questions

### Q: Do we need to refactor existing tests?
**A**: No. These are purely additive. Existing 215 tests remain unchanged.

### Q: Will this slow down test runs?
**A**: No. Integration tests still use fake LLM (same speed). Can add optional live-mode tests separately.

### Q: What about CI/CD integration?
**A**: Standard pytest. Should integrate cleanly with existing CI.

### Q: Do we need real Ollama for Phase 1?
**A**: No. Fake LLM works fine. Real validation is optional Phase 3.

### Q: How do we handle test data?
**A**: Deterministic seeds + fluent builders. No fixtures checked into Git (except templates).

---

## Risk Assessment

| Risk | Mitigation | Confidence |
|------|-----------|------------|
| Over-engineering | Start with Phase 1 only | High |
| Incomplete design | Detailed spec provided | High |
| Difficult to merge | Commit stage-by-stage | High |
| Flaky tests | Fixed seeds + monkeypatch | High |
| Lost in design | Step-by-step examples | High |

**Overall**: Low risk, high confidence

---

## Next Action

1. **Today**: Read IMPLEMENTATION_SUMMARY.md
2. **This week**: Read SIMULATOR_HARNESS_DESIGN.md
3. **Next week**: Start Phase 1 implementation

---

## Reference

- **Full audit**: AUDIT_TEST_COVERAGE.md
- **Implementation spec**: SIMULATOR_HARNESS_DESIGN.md
- **Summary**: IMPLEMENTATION_SUMMARY.md
- **Architecture**: ARCHITECTURE.md (existing)
- **Test examples**: See "Integration Tests: Examples" in SIMULATOR_HARNESS_DESIGN.md

---

**Status**: Ready to implement
**Effort**: 20-30 hours (Phase 1-2)
**Impact**: +50 tests, 90% coverage
**Complexity**: Moderate (but well-documented)

Good luck! 🚀
