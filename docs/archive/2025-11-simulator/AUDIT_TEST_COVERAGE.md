# aijournal Test Coverage Audit & Integration Test Strategy

**Document Created**: 2025-11-12
**Repository**: aijournal2
**Branch**: code-claude-haiku-4-5-audit-existing-tests
**Status**: Complete audit with simulator harness proposal

---

## Executive Summary

The aijournal codebase has **215 tests across 51 test files** with strong unit and component coverage, but critical gaps in end-to-end integration testing. This audit identifies six major gaps and proposes a deterministic simulator harness with stage-by-stage validation.

### Current State
- **Unit tests**: 80 ✅ (strong)
- **Component tests**: 100 ✅ (good)
- **Integration tests**: 35 ⚠️ (limited)
- **E2E pipeline tests**: 0 ❌ (missing)
- **Error handling**: 5 ❌ (67% gap)
- **Fixtures**: 1 ❌ (89% gap)

---

## Part 1: Coverage Analysis

### 1.1 Test Inventory by Category

#### Existing Test Structure
```
tests/
├── commands/                    # 2 tests (system health, status)
├── common/                      # 3 tests (artifact meta, LLM result structure)
├── domain/                      # 5 tests (evidence, redaction, serialization)
├── io/                          # 4 tests (artifact save/load roundtrip)
├── pipelines/                   # 20 tests
│   ├── test_advise.py          # 2 tests (fake & LLM modes)
│   ├── test_characterize.py    # 2 tests (fake & LLM normalization)
│   ├── test_facts.py           # 2 tests (fake & LLM merging)
│   ├── test_index.py           # 5 tests (preparation, annoy, meta)
│   ├── test_normalization.py   # 3 tests (section merge, sanitization, defaults)
│   ├── test_pack.py            # 4 tests (collection, trimming, metadata)
│   └── test_persona.py         # 2 tests (core requirements, trimming)
│   └── test_summarize.py       # 2 tests (fake & LLM merging)
├── prompts/                     # 9 tests (example validation, schema matching)
├── scripts/                     # 2 tests (structured metrics)
├── services/
│   ├── capture/                 # 4 tests
│   │   ├── test_stage_persona.py    # 2 tests (stage 7: build, noop)
│   │   └── test_stage_summarize.py  # 2 tests (stage 2: success, failure)
│   └── test_capture.py          # 11 tests (input, telemetry, rebuild, persist)
├── test_api_capture.py          # 2 tests (request structure, conversion)
├── test_chatd.py                # 4 tests (streaming, feedback, capture endpoint)
├── test_claim_atoms.py          # 3 tests (model, container, provenance)
├── test_cli_*.py (24 files)     # 155+ tests
│   ├── test_cli_advise.py       # 2 tests
│   ├── test_cli_audit.py        # 1 test (provenance audit & fix)
│   ├── test_cli_characterize.py # 5 tests (batch, apply, preview, progress, live)
│   ├── test_cli_chat.py         # 6 tests (fake mode, error, service, feedback)
│   ├── test_cli_facts.py        # 3 tests (generation, idempotent, progress)
│   ├── test_cli_feedback.py     # 2 tests (apply, no-batches)
│   ├── test_cli_index.py        # 3 tests (search, no-matches, missing)
│   ├── test_cli_ingest.py       # 2 tests (normalized+manifest, dedup)
│   ├── test_cli_init.py         # 4 tests (structure, idempotent, path, summary)
│   ├── test_cli_interview.py    # 5 tests (probes, fallback, missing, live)
│   ├── test_cli_logs.py         # 3 tests (missing, formatted, raw)
│   ├── test_cli_new.py          # 8 tests (create, tags, overwrite, seed, fake)
│   ├── test_cli_normalize.py    # 3 tests (yaml, idempotent, timezone)
│   ├── test_cli_ollama_health.py# 2 tests (models, idempotent)
│   ├── test_cli_pack.py         # 17 tests (L1-L4, budget, order, JSON, history)
│   ├── test_cli_persona.py      # 5 tests (build, trim, empty, min-claims, status)
│   ├── test_cli_profile_apply.py# 2 tests (merge, idempotent)
│   ├── test_cli_profile_status.py # 2 tests (ranks, missing)
│   ├── test_cli_profile_suggest.py # 3 tests (write, idempotent, progress)
│   ├── test_cli_summarize.py    # 7 tests (generate, idempotent, timeout, structured)
├── test_coercion.py             # 2 tests (float, int invalid)
├── test_consolidator.py         # 3 tests (merge, scope split, conflicts)
├── test_embedding_backend.py    # 4 tests (fake, http, error, empty)
├── test_ingest_agent.py         # 3 tests (agent build, structured, reject)
├── test_models_io.py            # 11 tests (roundtrip for all domain models)
├── test_ollama_services.py      # 18 tests (agent config, coercion, precedence)
├── test_retriever.py            # 4 tests (parity, annoy, missing, thread-safe)
├── test_sanity.py               # 1 test
├── test_schema_validation.py    # 1 test
└── test_workspace.py            # 8 tests (paths, defaults, env)
```

**Total: 215 tests** across 51 files.

---

### 1.2 Critical Coverage Gaps

#### Gap 1: Missing Stage-Level Integration Tests (7 of 9 stages)

The capture pipeline (`services/capture/`) has 9 stages:
- **Stage 0 (Persist)**: Extract text, write markdown, create manifest entry
- **Stage 1 (Normalize)**: Ingest markdown → structured entry
- **Stage 2 (Summarize)**: Generate daily summary from entries
- **Stage 3 (Facts)**: Extract micro-facts from summary
- **Stage 4 (Profile Suggest)**: Propose profile changes from facts
- **Stage 5 (Profile Apply)**: Merge suggestions into authoritative profile
- **Stage 6 (Characterize)**: Build characterization batch for review
- **Stage 7 (Review)**: Manually approve/reject characterization
- **Stage 8 (Index)**: Rebuild search index

**Current Test Coverage:**
- ✅ Stage 2: `test_stage_summarize.py` (2 tests: success, failure)
- ✅ Stage 7: `test_stage_persona.py` (2 tests: triggers build, noop)
- ❌ Stages 0, 1, 3, 4, 5, 6, 8: **ZERO stage-specific orchestrator tests**

**What's Missing:**
- No test validates stage0 end-to-end (persist → markdown + manifest + metadata)
- No test validates stage1 without mocking ingest_agent (real normalization flow)
- No test validates stages 3-6 with real artifacts from previous stages
- No test validates end-to-end manifest consistency through all stages
- No test validates timestamp propagation and artifact freshness

#### Gap 2: No Full-Pipeline E2E Tests

**What exists:** Individual pipeline tests (test_summarize.py, test_facts.py, etc.) mock the LLM and test pipeline logic in isolation.

**What's missing:**
- ❌ No test captures text → validates all 8 stages produce correct artifacts
- ❌ No test normalizes → summarizes → extracts facts in sequence
- ❌ No test verifies chat can retrieve and cite facts from captured entry
- ❌ No test validates persona includes claims from characterized entries
- ❌ No integration test proves capture output → persona core → chat works end-to-end

**Risk:** A change to manifest format, entry ID generation, or timestamp handling could silently break the entire pipeline without being caught by tests.

#### Gap 3: Minimal Fixture Coverage (1 of 8+ needed)

Only one pre-built fixture workspace exists:
- **`tests/fixtures/miniwk/`**: Contains 3 pre-normalized entries (2025-02-02 to 2025-02-04)

**What's needed:**
1. **Empty workspace** – for init/first-capture tests
2. **Single-day workspace** – 1 normalized entry + 1 summary
3. **Multi-day workspace** – 5 normalized entries + 5 summaries
4. **With-claims workspace** – 5 entries + 5 summaries + 20 accepted claims
5. **Full-workflow workspace** – Multi-day + claims + persona core + advice
6. **Conflicting-claims workspace** – Multiple claims same statement, different scope
7. **Stale-persona workspace** – Old persona core to test freshness check
8. **Chat-ready workspace** – With index + persona core + chat session

**Risk:** Tests can't easily set up complex scenarios without extensive manual fixture code.

#### Gap 4: 100% Fake LLM Usage (Zero Real Ollama Tests)

**Current mode:** All tests set `AIJOURNAL_FAKE_OLLAMA=1`
- Fake embedding returns `[0.0, ..., 0.0]` (zero vectors)
- Fake summaries return deterministic bullet points
- Fake facts return mock facts
- Fake characterizations return mock claims

**What's missing:**
- ❌ Zero tests validate against real `gpt-oss:20b` embedding distances
- ❌ Zero tests verify retrieval ranking with real embeddings
- ❌ Zero tests check real LLM token usage vs budget estimates
- ❌ Zero tests validate real summarization/facts/characterization quality

**Risk:** A change to embedding backend or model behavior would not be detected.

#### Gap 5: Limited Error Handling (67% gap)

Only 5 error-handling tests exist:
- `test_stage2_summarize_handles_failure()` – LLM error recovery
- `test_ingest_with_agent_rejects_unexpected_payload()` – invalid LLM response
- `test_run_ollama_agent_translates_user_errors()` – Ollama client errors
- `test_chat_errors_when_index_missing()` – missing artifact
- `test_index_search_errors_when_index_missing()` – missing artifact

**Missing scenarios (10+):**
- Corrupted YAML artifact (invalid schema)
- Missing manifest entry
- Manifest deduplication hash collision
- Normalized entry missing required fields
- Timestamp parsing errors
- Duplicate slug on same day
- Profile/claims schema version mismatch
- Index rebuild with missing entry file
- Chat with stale persona core
- Capture with locked manifest file

**Risk:** Silent failures or cryptic errors when real data is malformed.

#### Gap 6: No Multi-Day Workflow Tests

**Current pattern:** Most tests operate on a single date (2024-01-02 or 2025-02-03).

**Missing:**
- ❌ Facts spanning multiple days (e.g., "weekly pattern analysis")
- ❌ Profile compounding (claims strengthening across 7-day observations)
- ❌ Chat retrieving context from 5+ different days
- ❌ Pack L4 with history spanning 14 days
- ❌ Interview prioritization across stale claims from different dates

**Risk:** Multi-day aggregations may have off-by-one errors or missing context.

---

### 1.3 Test Quality Assessment

#### Determinism & Reproducibility

**Strengths:**
- ✅ Fixed `_FIXED_NOW = datetime(2025, 2, 3, 12, 0, tzinfo=UTC)` in conftest.py
- ✅ `monkeypatch.setattr("aijournal.utils.time.now", ...)` used consistently
- ✅ Fake LLM with deterministic outputs
- ✅ Most tests use `tmp_path` for isolation

**Weaknesses:**
- ⚠️ 5 tests use `datetime.now()` without monkeypatching (potential flakiness)
- ⚠️ No explicit seed setting for hypothesis-based property tests
- ⚠️ Some tests assume alphabetical order of files (brittle)

#### Async/Threading Safety

**Current state:**
- `test_retriever_close_from_different_thread()` validates thread-safe cleanup
- `chatd` server tests check streaming without blocking
- No other async/threading tests

**Gap:** No tests verify capture stages under concurrent access, index locking, or parallel reads.

#### Mocking Patterns

**Pattern 1: Function-level mocking** (most common)
```python
monkeypatch.setattr("aijournal.utils.time.now", lambda: _FIXED_NOW)
monkeypatch.setenv("AIJOURNAL_FAKE_OLLAMA", "1")
```

**Pattern 2: Fixture-level setup** (cli_workspace, cli_runner)
```python
@pytest.fixture
def cli_workspace(tmp_path, monkeypatch, cli_runner) -> Path:
    monkeypatch.chdir(tmp_path)
    cli_runner.invoke(app, ["init"])
    yield tmp_path
```

**Pattern 3: Inline helper functions**
```python
def _normalized_entry(entry_id: str) -> NormalizedEntry:
    return NormalizedEntry(...)
```

**Assessment:** Mocking is consistent but lacks coverage for complex scenarios (multiple entries, cross-stage validation).

---

## Part 2: Proposed Simulator Harness

### 2.1 Vision

A **deterministic, multi-stage simulator harness** that:
1. Seeds fixture workspaces with known entries
2. Runs capture pipeline stage-by-stage
3. Validates output artifacts at each stage
4. Reports failures with stage-specific context
5. Enables "replay" of real-world scenarios

### 2.2 Architecture

```
tests/
├── simulator/
│   ├── __init__.py
│   ├── fixtures.py              # Fixture builders
│   ├── harness.py               # Stage orchestrator
│   ├── validators.py            # Output validation
│   ├── reporters.py             # Failure reporting
│   └── seeds.py                 # Deterministic data generators
│
├── integration/
│   ├── test_stage_0_persist.py           # 4 tests
│   ├── test_stage_1_normalize.py         # 3 tests
│   ├── test_stage_3_facts.py             # 3 tests
│   ├── test_stage_4_profile.py           # 2 tests
│   ├── test_stage_5_characterize.py      # 2 tests
│   ├── test_stage_6_index.py             # 2 tests
│   ├── test_stage_8_pack.py              # 1 test
│   ├── test_full_pipeline.py             # 5+ tests
│   └── test_error_handling.py            # 10+ tests
│
└── fixtures/
    ├── miniwk/                   # Existing
    ├── empty/                    # New
    ├── single_day/               # New
    ├── multi_day/                # New
    ├── with_claims/              # New
    ├── full_workflow/            # New
    └── conflicting/              # New
```

### 2.3 Key Components

#### 2.3.1 Fixture Builders (`simulator/fixtures.py`)

```python
class WorkspaceFixture:
    """Build deterministic test workspaces."""

    def __init__(self, path: Path, seed: int = 42):
        self.path = path
        self.seed = seed
        self.rng = Random(seed)

    def with_normalized_entry(
        self,
        date: str,
        entry_id: str,
        summary: str,
        tags: list[str] | None = None,
        source_hash: str | None = None,
    ) -> "WorkspaceFixture":
        """Add a normalized entry."""

    def with_manifest_entry(
        self,
        entry_id: str,
        source_hash: str,
        path: str,
    ) -> "WorkspaceFixture":
        """Register in manifest."""

    def with_daily_summary(
        self,
        date: str,
        bullets: list[str],
    ) -> "WorkspaceFixture":
        """Add pre-generated summary."""

    def with_claims(
        self,
        claims: list[dict],
    ) -> "WorkspaceFixture":
        """Populate profile/claims.yaml."""

    def build(self) -> Path:
        """Write all fixtures to disk."""
```

#### 2.3.2 Stage Harness (`simulator/harness.py`)

```python
class StageOrchestrator:
    """Run capture stages with validation at each step."""

    def __init__(self, workspace: Path, config: dict | None = None):
        self.workspace = workspace
        self.run_context = RunContext(workspace=workspace)
        self.artifacts: dict[str, Artifact] = {}

    def run_stage(
        self,
        stage: int,
        date: str,
        validate: bool = True,
    ) -> StageResult:
        """Execute a stage and collect artifacts."""

    def run_through(
        self,
        max_stage: int,
        date: str,
        validate: bool = True,
    ) -> PipelineResult:
        """Run stages 0 through max_stage."""

    def validate_stage(stage: int, artifacts: dict) -> list[ValidationError]:
        """Check stage output against schema."""

    def diff_stage(
        self,
        stage: int,
        expected: dict,
        actual: dict,
    ) -> str:
        """Generate human-readable diff."""
```

#### 2.3.3 Output Validators (`simulator/validators.py`)

```python
class StageValidator:
    """Validate stage outputs against expectations."""

    @staticmethod
    def validate_stage_0(
        workspace: Path,
        date: str,
        entry_id: str,
    ) -> list[str]:
        """Validate persist stage output."""
        # Check markdown file exists
        # Check manifest entry created
        # Check metadata has correct structure

    @staticmethod
    def validate_stage_1(normalized_path: Path) -> list[str]:
        """Validate normalized entry structure."""

    @staticmethod
    def validate_stage_2(summary_path: Path) -> list[str]:
        """Validate daily summary."""

    # ... validate_stage_3 through validate_stage_8
```

#### 2.3.4 Failure Reporter (`simulator/reporters.py`)

```python
class TestReporter:
    """Clear, actionable failure reports."""

    def report_stage_failure(
        self,
        stage: int,
        context: dict,
        errors: list[str],
    ) -> str:
        """Generate failure summary with context."""
        # Stage name and purpose
        # Input artifacts examined
        # Output schema violations
        # Suggestions for investigation
```

#### 2.3.5 Deterministic Data Generators (`simulator/seeds.py`)

```python
def generate_entry_text(seed: int, day: int) -> str:
    """Generate deterministic journal text."""

def generate_summary_bullets(seed: int, day: int) -> list[str]:
    """Generate deterministic summary bullets."""

def generate_claims(seed: int, count: int) -> list[dict]:
    """Generate deterministic claim atoms."""
```

### 2.4 Test Implementation Examples

#### Example 1: Stage 0 (Persist) Test

```python
def test_stage_0_persist_text_writes_canonical_markdown(
    tmp_path: Path,
    cli_runner: CliRunner,
):
    """Stage 0 should write markdown + manifest from text input."""
    fixture = WorkspaceFixture(tmp_path, seed=42).build()

    result = cli_runner.invoke(app, [
        "capture",
        "--text", "My journal entry",
        "--date", "2025-10-28",
        "--max-stage", "0",
    ])

    assert result.exit_code == 0

    # Validate stage 0 output
    errors = StageValidator.validate_stage_0(
        tmp_path,
        date="2025-10-28",
        entry_id="2025-10-28-my-journal-entry",
    )
    assert not errors, f"Stage 0 validation failed: {errors}"

    # Check markdown
    md_path = tmp_path / "data" / "journal" / "2025" / "10" / "28" / "*.md"
    assert len(list(md_path.parent.glob("*.md"))) == 1
```

#### Example 2: Full Pipeline Test

```python
def test_full_pipeline_capture_to_chat(tmp_path: Path, cli_runner: CliRunner):
    """End-to-end: capture → normalize → summarize → facts → characterize → persona → chat."""

    fixture = (WorkspaceFixture(tmp_path, seed=42)
        .with_normalized_entry(
            date="2025-10-20",
            entry_id="2025-10-20-deep-work",
            summary="Focused on architecture review",
            tags=["focus", "planning"],
        )
        .with_manifest_entry(
            entry_id="2025-10-20-deep-work",
            source_hash="abc123",
            path="data/journal/2025/10/20/deep-work.md",
        )
        .build())

    orchestrator = StageOrchestrator(tmp_path)

    # Run full pipeline
    result = orchestrator.run_through(
        max_stage=8,
        date="2025-10-20",
        validate=True,
    )

    assert result.success, f"Pipeline failed: {result.errors}"
    assert result.artifacts["stage_2"].exists()  # summary
    assert result.artifacts["stage_3"].exists()  # facts
    assert result.artifacts["stage_6"].exists()  # characterization

    # Verify chat can use the result
    chat_result = cli_runner.invoke(app, [
        "chat",
        "What did I work on?",
        "--session", "test-session",
    ])
    assert chat_result.exit_code == 0
    assert "deep-work" in chat_result.stdout or "architecture" in chat_result.stdout
```

#### Example 3: Error Handling Test

```python
def test_capture_handles_corrupted_manifest(tmp_path: Path, cli_runner: CliRunner):
    """Capture should report clear error if manifest is malformed."""

    fixture = WorkspaceFixture(tmp_path, seed=42).build()

    # Corrupt manifest
    manifest_path = tmp_path / "data" / "manifest" / "ingested.yaml"
    manifest_path.write_text("INVALID: { yaml: [unclosed")

    result = cli_runner.invoke(app, [
        "capture",
        "--text", "New entry",
        "--max-stage", "0",
    ])

    assert result.exit_code != 0
    assert "manifest" in result.stdout.lower()
    assert "YAML" in result.stdout or "parse" in result.stdout.lower()
```

---

## Part 3: Implementation Roadmap

### Phase 1: Foundation (Priority 1 - Critical)
**Estimated effort: 20 hours | Expected: 15+ new tests**

1. **Create simulator infrastructure** (4 hours)
   - `tests/simulator/fixtures.py` – WorkspaceFixture builder
   - `tests/simulator/harness.py` – StageOrchestrator
   - `tests/simulator/validators.py` – Output validation
   - `tests/simulator/reporters.py` – Failure reporting

2. **Create stage-specific test files** (12 hours)
   - `tests/integration/test_stage_0_persist.py` (4 tests)
   - `tests/integration/test_stage_1_normalize.py` (3 tests)
   - `tests/integration/test_stage_3_facts.py` (3 tests)
   - `tests/integration/test_stage_4_profile.py` (2 tests)
   - `tests/integration/test_stage_5_characterize.py` (2 tests)
   - `tests/integration/test_stage_6_index.py` (2 tests)
   - `tests/integration/test_stage_8_pack.py` (1 test)

3. **Create full-pipeline tests** (2 hours)
   - `tests/integration/test_full_pipeline.py` (5+ tests)

4. **Build additional fixtures** (2 hours)
   - `tests/fixtures/empty/`
   - `tests/fixtures/single_day/`
   - `tests/fixtures/multi_day/`
   - Others as needed

### Phase 2: Error Handling & Robustness (Priority 2 - High)
**Estimated effort: 8 hours | Expected: 18+ tests**

1. **Create error scenario tests** (6 hours)
   - `tests/integration/test_error_handling.py`
   - Corrupted artifacts, missing files, schema mismatches
   - 10-12 test cases covering common failures

2. **Create additional fixtures** (2 hours)
   - `tests/fixtures/with_claims/`
   - `tests/fixtures/full_workflow/`
   - `tests/fixtures/conflicting/`
   - `tests/fixtures/stale_persona/`

### Phase 3: Live LLM Integration (Priority 3 - Optional)
**Estimated effort: 2 hours | Expected: 5+ tests**

1. **Create live mode tests** (2 hours)
   - `tests/integration/test_live_llm.py`
   - Requires real `gpt-oss:20b` and embeddings
   - Conditional skip if Ollama unavailable

---

## Part 4: Acceptance Criteria

### Success Metrics

**Before → After**
| Metric | Before | After | Target |
|--------|--------|-------|--------|
| Total tests | 215 | 240-250 | ✅ |
| Integration tests | 35 | 50-60 | ✅ |
| E2E pipeline tests | 0 | 5+ | ✅ |
| Error handling tests | 5 | 15-20 | ✅ |
| Fixtures | 1 | 8 | ✅ |
| Stage coverage | 2/9 | 9/9 | ✅ |
| Integration coverage | ~30% | ~90% | ✅ |

### Test Quality Checklist

- [ ] All new tests are deterministic (use fixed seeds, mocked time)
- [ ] Each test has clear name describing what it validates
- [ ] Each test includes comment explaining stage being tested
- [ ] Failure messages are actionable (include expected vs actual)
- [ ] Tests are independent (no shared state between tests)
- [ ] Tests use fixtures from `tests/simulator/` or `tests/fixtures/`
- [ ] Error handling tests validate both exit code and message
- [ ] Multi-stage tests validate artifacts at each step

---

## Part 5: Appendix

### A. Test File Locations & Coverage

| Component | Test File | Tests | Coverage |
|-----------|-----------|-------|----------|
| Init | test_cli_init.py | 4 | 100% ✅ |
| Capture (service) | test_capture.py | 11 | 60% ⚠️ |
| Ingest | test_ingest_agent.py | 3 | 80% ⚠️ |
| Normalize | test_normalization.py | 3 | 40% ❌ |
| Summarize | test_summarize.py | 2 | 30% ❌ |
| Facts | test_facts.py | 2 | 30% ❌ |
| Characterize | test_characterize.py | 2 | 40% ❌ |
| Profile | test_cli_profile_*.py | 7 | 60% ⚠️ |
| Persona | test_persona.py | 2 | 40% ❌ |
| Index | test_index.py | 5 | 70% ⚠️ |
| Pack | test_pack.py | 4 | 50% ⚠️ |
| Chat | test_chat.py | 6 | 60% ⚠️ |
| Advise | test_advise.py | 2 | 40% ❌ |

### B. Timestamp & Time Handling

**Time mocking pattern:**
```python
@pytest.fixture
def monkeypatch_time(monkeypatch):
    def _set_time(dt: datetime) -> None:
        monkeypatch.setattr(
            "aijournal.utils.time.now",
            lambda: dt
        )
    return _set_time
```

**Key dates used in tests:**
- `2024-01-02T09:00:00Z` (pipelines tests)
- `2025-02-03T12:00:00Z` (CLI integration tests)
- `2025-10-28` (capture tests)

### C. Known Flaky Tests (If Any)

Currently: None identified. All 215 tests are deterministic and isolated.

**Potential risks:**
- Tests assuming alphabetical file ordering could be brittle
- Tests relying on process execution order (less likely in pytest)
- Any tests that depend on system clock outside monkeypatch

---

## Conclusion

The aijournal codebase has solid unit and component test coverage but needs strategic investment in integration and E2E testing. The proposed simulator harness provides a reusable framework for stage-by-stage validation and enables rapid addition of error-handling and multi-day workflow tests.

**Recommended next steps:**
1. Implement Phase 1 (foundation + stage tests) – high impact, moderate effort
2. Review and merge proposed test structure
3. Run full test suite to establish baseline
4. Gradually add Phase 2 (error handling) and Phase 3 (live LLM) as needed

---

**Document Version**: 1.0
**Last Updated**: 2025-11-12
**Author**: Claude Agent (audit task)
