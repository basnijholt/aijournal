# Simulator Harness: Detailed Design & Implementation Guide

**Purpose**: Enable deterministic, stage-by-stage validation of the aijournal capture pipeline with clear failure reporting.

---

## Quick Overview

### The Problem
- 215 existing tests are mostly unit-level with fake LLM
- No tests validate full pipeline execution (stage 0-8)
- 7 of 9 capture stages have zero dedicated tests
- Failures in real workflows aren't caught by current test suite

### The Solution
- **Simulator Harness**: Orchestrate stages sequentially
- **Fixture Builders**: Generate deterministic test workspaces
- **Output Validators**: Check each stage's artifacts
- **Clear Reporters**: Actionable failure messages

### Expected Outcomes
- 50-60 new integration tests
- 9/9 capture stages covered (currently 2/9)
- End-to-end pipeline validation
- 10-20 error scenario tests
- 8 reusable fixture templates

---

## File Structure

```
tests/
├── simulator/
│   ├── __init__.py
│   ├── fixtures.py              # WorkspaceFixture class
│   ├── harness.py               # StageOrchestrator class
│   ├── validators.py            # Artifact validation
│   ├── reporters.py             # Failure reporting
│   └── seeds.py                 # Deterministic generators
│
├── integration/
│   ├── __init__.py
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
    ├── miniwk/                   # Existing: 3 entries, 2025-02-02 to 04
    ├── empty/                    # Empty workspace (no entries)
    ├── single_day/               # 1 entry, 1 summary
    ├── multi_day/                # 5 entries, 5 summaries
    ├── with_claims/              # Multi-day + 20 claims
    ├── full_workflow/            # Multi-day + claims + persona + advice
    ├── conflicting/              # Conflicting claims (same statement, diff scope)
    └── stale_persona/            # Old persona core (to test freshness)
```

---

## Module 1: `simulator/fixtures.py`

### Purpose
Build deterministic test workspaces with fluent API.

### Class: `WorkspaceFixture`

```python
class WorkspaceFixture:
    """
    Build a complete test workspace with controlled entries, summaries, claims, etc.

    Example:
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
            .with_daily_summary(
                date="2025-10-20",
                bullets=["Did architecture review", "Wrote design doc"],
            )
            .build())
    """

    def __init__(self, path: Path, seed: int = 42) -> None:
        """Initialize with workspace path and deterministic seed."""
        self.path = path
        self.seed = seed
        self.rng = Random(seed)
        self._entries: list[dict] = []
        self._manifest: list[dict] = []
        self._summaries: list[dict] = []
        self._claims: list[dict] = []

    def with_normalized_entry(
        self,
        date: str,
        entry_id: str,
        summary: str,
        title: str | None = None,
        tags: list[str] | None = None,
        sections: list[dict] | None = None,
        source_hash: str | None = None,
    ) -> "WorkspaceFixture":
        """
        Add a normalized entry to the workspace.

        Args:
            date: ISO date (2025-10-20)
            entry_id: Unique entry identifier
            summary: One-line summary
            title: Override generated title (defaults to first word of summary)
            tags: List of tags (defaults to [])
            sections: List of {heading, summary} dicts
            source_hash: SHA256 hash (generated if not provided)

        Returns:
            self for chaining
        """

    def with_manifest_entry(
        self,
        entry_id: str,
        source_hash: str,
        path: str,
        source_type: str = "journal",
        ingested_at: str | None = None,
    ) -> "WorkspaceFixture":
        """Register entry in manifest."""

    def with_daily_summary(
        self,
        date: str,
        bullets: list[str] | None = None,
        highlights: list[str] | None = None,
        todo_candidates: list[str] | None = None,
    ) -> "WorkspaceFixture":
        """Add a DailySummary artifact."""

    def with_microfacts(
        self,
        date: str,
        facts: list[dict],
    ) -> "WorkspaceFixture":
        """Add MicroFacts artifact."""

    def with_claims(
        self,
        claims: list[dict],
        status: str = "accepted",
    ) -> "WorkspaceFixture":
        """Add accepted claims to profile/claims.yaml."""

    def with_self_profile(self, profile: dict) -> "WorkspaceFixture":
        """Set custom self_profile.yaml (merges with defaults)."""

    def with_persona_core(
        self,
        claims: list[dict],
        profile: dict | None = None,
    ) -> "WorkspaceFixture":
        """Add pre-built persona_core.yaml."""

    def build(self) -> Path:
        """
        Write all configured artifacts to disk and return workspace path.

        Creates:
        - data/normalized/<date>/*.yaml
        - data/manifest/ingested.yaml
        - derived/summaries/<date>.yaml
        - derived/microfacts/<date>.yaml
        - profile/claims.yaml
        - profile/self_profile.yaml
        - derived/persona/persona_core.yaml (if provided)

        Returns:
            Path to workspace root
        """
```

### Helper Functions

```python
def _normalize_claim(claim: dict, seed: int) -> dict:
    """Ensure claim has all required fields with deterministic defaults."""

def _generate_entry_id(date: str, summary: str, seed: int) -> str:
    """Generate deterministic entry ID from date and summary."""

def _ensure_manifest_entry(
    entry_id: str,
    path: str,
    source_hash: str | None = None,
) -> dict:
    """Create manifest entry with required fields."""
```

### Usage Example

```python
def test_example():
    fixture = (WorkspaceFixture(tmp_path, seed=42)
        .with_normalized_entry(
            date="2025-10-20",
            entry_id="2025-10-20-deep-work",
            summary="Focused on architecture review",
            tags=["focus", "planning"],
        )
        .with_normalized_entry(
            date="2025-10-21",
            entry_id="2025-10-21-meetings",
            summary="Attended team standup and planning",
            tags=["social", "planning"],
        )
        .with_manifest_entry(
            entry_id="2025-10-20-deep-work",
            source_hash="abc123",
            path="data/journal/2025/10/20/deep-work.md",
        )
        .with_manifest_entry(
            entry_id="2025-10-21-meetings",
            source_hash="def456",
            path="data/journal/2025/10/21/meetings.md",
        )
        .build())

    assert (fixture / "data" / "normalized" / "2025-10-20").exists()
    assert (fixture / "data" / "manifest" / "ingested.yaml").exists()
```

---

## Module 2: `simulator/harness.py`

### Purpose
Orchestrate capture stages with validation at each step.

### Class: `StageOrchestrator`

```python
class StageOrchestrator:
    """
    Run capture stages sequentially, collecting and validating artifacts.

    Example:
        orchestrator = StageOrchestrator(workspace, config={...})
        result = orchestrator.run_through(max_stage=8, date="2025-10-20")
        assert result.success
        assert result.artifacts["stage_2"].exists()  # summary
    """

    def __init__(
        self,
        workspace: Path,
        config: dict | None = None,
        verbose: bool = False,
    ) -> None:
        """
        Initialize orchestrator for a workspace.

        Args:
            workspace: Path to initialized workspace
            config: Override config.yaml values
            verbose: Print stage progress
        """

    def run_stage(
        self,
        stage: int,
        date: str,
        entry_ids: list[str] | None = None,
        validate: bool = True,
    ) -> StageResult:
        """
        Execute a single stage and return result.

        Args:
            stage: Stage number (0-8)
            date: ISO date to process
            entry_ids: Specific entries to process (defaults to all)
            validate: Check output against schema

        Returns:
            StageResult with success, artifacts, errors, duration
        """

    def run_through(
        self,
        max_stage: int,
        date: str,
        validate: bool = True,
        stop_on_error: bool = True,
    ) -> PipelineResult:
        """
        Run stages 0 through max_stage sequentially.

        Args:
            max_stage: Final stage to run
            date: ISO date to process
            validate: Check each stage output
            stop_on_error: Halt on first error (else collect all)

        Returns:
            PipelineResult with all stage results
        """

    def get_artifact(
        self,
        stage: int,
        date: str,
        kind: str,
    ) -> Artifact | None:
        """Retrieve generated artifact by stage and kind."""

    def diff_artifact(
        self,
        stage: int,
        expected: dict,
        actual: Path,
    ) -> str:
        """Generate human-readable diff for validation failure."""
```

### Data Classes

```python
@dataclass
class StageResult:
    """Result of running a single stage."""
    stage: int
    date: str
    success: bool
    duration: float
    artifacts: dict[str, Path]  # kind -> path
    errors: list[str]
    warnings: list[str]
    telemetry: dict[str, Any]   # From stage logs

@dataclass
class PipelineResult:
    """Result of running multiple stages."""
    success: bool
    stages: list[StageResult]
    artifacts: dict[str, Path]  # stage_X -> path
    errors: list[str]
    total_duration: float

    @property
    def failed_stage(self) -> int | None:
        """Return first failed stage number, or None."""
```

### Usage Example

```python
def test_pipeline_integration():
    fixture = WorkspaceFixture(tmp_path, seed=42).build()

    orchestrator = StageOrchestrator(fixture)
    result = orchestrator.run_through(
        max_stage=3,  # Persist → Normalize → Summarize → Facts
        date="2025-10-20",
        validate=True,
    )

    assert result.success, f"Pipeline failed at stage {result.failed_stage}"
    assert len(result.stages) == 4  # 0, 1, 2, 3
    assert all(s.success for s in result.stages)

    summary_path = result.artifacts.get("stage_2")
    assert summary_path and summary_path.exists()
```

---

## Module 3: `simulator/validators.py`

### Purpose
Validate stage output artifacts against expected schema and content.

### Class: `StageValidator`

```python
class StageValidator:
    """Validate artifacts produced by each stage."""

    @staticmethod
    def validate_stage_0(
        workspace: Path,
        date: str,
        entry_id: str | None = None,
    ) -> ValidationResult:
        """
        Validate Stage 0 (Persist) output.

        Checks:
        - Markdown file created under data/journal/YYYY/MM/DD/
        - Manifest entry added to data/manifest/ingested.yaml
        - Manifest entry has required fields (hash, path, id, source_type)
        - No duplicate hashes in manifest
        """

    @staticmethod
    def validate_stage_1(
        workspace: Path,
        date: str,
        entry_id: str,
    ) -> ValidationResult:
        """
        Validate Stage 1 (Normalize) output.

        Checks:
        - Normalized YAML created under data/normalized/YYYY-MM-DD/
        - All required fields present (id, created_at, title, tags, summary)
        - Tags are slugified and deduplicated
        - Sections have correct structure
        - Source hash matches manifest
        """

    @staticmethod
    def validate_stage_2(
        workspace: Path,
        date: str,
    ) -> ValidationResult:
        """
        Validate Stage 2 (Summarize) output.

        Checks:
        - Summary YAML exists at derived/summaries/
        - DailySummary schema valid
        - Bullets not empty and deduplicated
        - TODO candidates present
        - Artifact meta includes model, prompt_hash
        """

    @staticmethod
    def validate_stage_3(
        workspace: Path,
        date: str,
    ) -> ValidationResult:
        """
        Validate Stage 3 (Facts) output.

        Checks:
        - MicroFacts YAML exists
        - Facts array populated
        - Each fact has id, statement, confidence, evidence
        - Provenance references valid entries
        - Claim proposals well-formed
        """

    # ... validate_stage_4 through validate_stage_8

    @staticmethod
    def validate_manifest(workspace: Path) -> ValidationResult:
        """Validate manifest.yaml structure and consistency."""

    @staticmethod
    def validate_claims_file(workspace: Path) -> ValidationResult:
        """Validate profile/claims.yaml structure."""

    @staticmethod
    def validate_persona_core(workspace: Path) -> ValidationResult:
        """Validate derived/persona/persona_core.yaml."""
```

### Data Classes

```python
@dataclass
class ValidationResult:
    """Result of validating a stage's output."""
    valid: bool
    stage: int | None
    errors: list[str]  # Schema violations
    warnings: list[str]  # Non-critical issues
    checked_files: list[Path]

    def report(self) -> str:
        """Human-readable validation report."""
```

---

## Module 4: `simulator/reporters.py`

### Purpose
Generate clear, actionable failure messages.

### Class: `TestReporter`

```python
class TestReporter:
    """Generate detailed failure reports."""

    @staticmethod
    def report_stage_failure(
        stage: int,
        date: str,
        entry_id: str | None,
        validation_errors: list[str],
        workspace: Path,
    ) -> str:
        """
        Generate detailed failure report.

        Example output:
        ```
        ╔════════════════════════════════════════╗
        ║ STAGE 2 (Summarize) VALIDATION FAILED  ║
        ╚════════════════════════════════════════╝

        Date: 2025-10-20
        Entry: 2025-10-20-deep-work

        Errors:
        1. Missing field 'bullets' in summary artifact
        2. Field 'highlights' is empty list (should have ≥1 item)

        Expected at:
        /tmp/pytest-123/derived/summaries/2025-10-20.yaml

        Checked files:
        - data/normalized/2025-10-20/2025-10-20-deep-work.yaml ✓
        - derived/summaries/2025-10-20.yaml ✗ (missing)

        Next steps:
        1. Verify normalize stage output at:
           /tmp/pytest-123/data/normalized/2025-10-20/
        2. Check summarize service logs in:
           /tmp/pytest-123/derived/logs/
        3. Run manually:
           aijournal ops pipeline summarize --date 2025-10-20 --verbose-json
        ```
        """

    @staticmethod
    def report_pipeline_failure(
        result: PipelineResult,
        workspace: Path,
    ) -> str:
        """Generate report for entire pipeline failure."""

    @staticmethod
    def report_artifact_diff(
        field: str,
        expected: Any,
        actual: Any,
    ) -> str:
        """Generate diff for specific field mismatch."""
```

---

## Module 5: `simulator/seeds.py`

### Purpose
Deterministic data generators for consistent test data.

### Functions

```python
def generate_entry_text(seed: int, day: int, day_count: int = 7) -> str:
    """
    Generate deterministic journal entry text.

    Args:
        seed: Random seed
        day: Day number (0-indexed within week)
        day_count: Total days to consider

    Returns:
        Multi-paragraph journal text with realistic structure
    """

def generate_summary_bullets(seed: int, day: int, count: int = 3) -> list[str]:
    """Generate deterministic daily summary bullets."""

def generate_tags(seed: int, day: int, count: int = 2) -> list[str]:
    """Generate deterministic tags."""

def generate_claim_atom(
    seed: int,
    claim_num: int,
    statement: str | None = None,
) -> dict:
    """Generate deterministic claim atom dict."""

def generate_entries(
    seed: int,
    date_range: tuple[str, str],
    entries_per_day: int = 1,
) -> list[tuple[str, str, str]]:  # (date, entry_id, summary)
    """Generate multiple deterministic entries across a date range."""
```

### Example

```python
# All uses of seed=42 + day=0 will always produce same text
text_1 = generate_entry_text(seed=42, day=0)
text_2 = generate_entry_text(seed=42, day=0)
assert text_1 == text_2  # Deterministic

# Different days produce different text
text_monday = generate_entry_text(seed=42, day=0)
text_tuesday = generate_entry_text(seed=42, day=1)
assert text_monday != text_tuesday  # But both deterministic
```

---

## Integration Tests: Examples

### File: `tests/integration/test_stage_0_persist.py`

```python
"""Stage 0 (Persist): Convert input to markdown + manifest."""

import pytest
from pathlib import Path
from typer.testing import CliRunner
from tests.simulator.fixtures import WorkspaceFixture
from tests.simulator.validators import StageValidator

@pytest.fixture
def cli_runner():
    return CliRunner()

def test_stage_0_persist_text_input_creates_markdown(
    tmp_path: Path,
    cli_runner: CliRunner,
):
    """Stage 0 should create markdown file from text input."""
    fixture = WorkspaceFixture(tmp_path, seed=42).build()

    result = cli_runner.invoke(app, [
        "capture",
        "--text", "My journal entry about deep work",
        "--date", "2025-10-28",
        "--max-stage", "0",
    ])

    assert result.exit_code == 0

    val_result = StageValidator.validate_stage_0(
        fixture,
        date="2025-10-28",
    )
    assert val_result.valid, "\n".join(val_result.errors)

def test_stage_0_persist_file_input_snapshots_raw(
    tmp_path: Path,
    cli_runner: CliRunner,
):
    """Stage 0 should snapshot raw file under data/raw/."""

def test_stage_0_persist_manifest_deduplication(
    tmp_path: Path,
    cli_runner: CliRunner,
):
    """Stage 0 should skip duplicate hashes in manifest."""

def test_stage_0_persist_slug_collision_handling(
    tmp_path: Path,
    cli_runner: CliRunner,
):
    """Stage 0 should handle duplicate slugs on same day."""
```

### File: `tests/integration/test_full_pipeline.py`

```python
"""End-to-end pipeline: capture through pack."""

def test_full_pipeline_single_entry_through_pack(
    tmp_path: Path,
    cli_runner: CliRunner,
):
    """End-to-end: text → markdown → summary → facts → profile → pack."""
    fixture = (WorkspaceFixture(tmp_path, seed=42)
        .with_normalized_entry(
            date="2025-10-20",
            entry_id="2025-10-20-deep-work",
            summary="Spent morning on architecture review and design decisions",
            tags=["focus", "planning"],
        )
        .with_manifest_entry(
            entry_id="2025-10-20-deep-work",
            source_hash="abc123",
            path="data/journal/2025/10/20/deep-work.md",
        )
        .build())

    # Initialize orchestrator
    orchestrator = StageOrchestrator(fixture)

    # Run full pipeline
    result = orchestrator.run_through(
        max_stage=8,
        date="2025-10-20",
        validate=True,
    )

    # Verify success
    assert result.success, TestReporter.report_pipeline_failure(result, fixture)
    assert all(s.success for s in result.stages)

    # Verify key artifacts
    assert result.artifacts.get("stage_2")  # summary
    assert result.artifacts.get("stage_3")  # facts
    assert result.artifacts.get("stage_8")  # pack

def test_full_pipeline_multi_day_persona_building(
    tmp_path: Path,
    cli_runner: CliRunner,
):
    """Multi-day: Building persona across 5 days of entries."""
```

### File: `tests/integration/test_error_handling.py`

```python
"""Error scenarios: corruption, missing files, schema mismatches."""

def test_capture_handles_corrupted_manifest(
    tmp_path: Path,
    cli_runner: CliRunner,
):
    """Capture should report error if manifest YAML is malformed."""
    fixture = WorkspaceFixture(tmp_path, seed=42).build()

    # Corrupt manifest
    manifest = fixture / "data" / "manifest" / "ingested.yaml"
    manifest.write_text("INVALID: { yaml: [unclosed")

    result = cli_runner.invoke(app, [
        "capture",
        "--text", "New entry",
        "--max-stage", "0",
    ])

    assert result.exit_code != 0
    assert "manifest" in result.stdout.lower() or "yaml" in result.stdout.lower()

def test_capture_handles_missing_normalized_entry(
    tmp_path: Path,
    cli_runner: CliRunner,
):
    """Summarize should error if normalized entry missing."""

def test_capture_handles_invalid_claim_schema(
    tmp_path: Path,
    cli_runner: CliRunner,
):
    """Profile should error if claims.yaml has invalid structure."""
```

---

## Fixtures Directory Structure

### Minimal Fixtures

```
tests/fixtures/empty/
├── data/
│   ├── journal/              # Empty
│   ├── normalized/           # Empty
│   ├── manifest/
│   │   └── ingested.yaml     # Empty list []
│   └── raw/                  # Empty
├── profile/
│   ├── claims.yaml           # Empty claims
│   └── self_profile.yaml     # Minimal profile
├── derived/
│   ├── summaries/            # Empty
│   ├── microfacts/           # Empty
│   └── logs/                 # Empty
└── config.yaml               # Default config
```

### Populated Fixtures (Serialized with WorkspaceFixture)

**single_day/**
- 1 normalized entry (2025-10-20)
- 1 manifest entry
- 1 daily summary

**multi_day/**
- 5 normalized entries (2025-10-16 to 2025-10-20)
- 5 manifest entries
- 5 daily summaries

**with_claims/**
- multi_day + 20 accepted claims
- profile/claims.yaml populated

**full_workflow/**
- with_claims + persona_core.yaml + advice artifacts
- Ready for chat/pack operations

---

## Implementation Checklist

### Foundation Phase (Week 1-2)

- [ ] Create `tests/simulator/__init__.py`
- [ ] Implement `fixtures.py` (WorkspaceFixture)
- [ ] Implement `harness.py` (StageOrchestrator)
- [ ] Implement `validators.py` (StageValidator)
- [ ] Implement `reporters.py` (TestReporter)
- [ ] Implement `seeds.py` (generators)
- [ ] Create `tests/integration/__init__.py`
- [ ] Create minimal fixtures in `tests/fixtures/`
- [ ] Write 4 Stage 0 tests
- [ ] Run tests and refine

### Integration Phase (Week 2-3)

- [ ] Write 3 Stage 1 tests
- [ ] Write 3 Stage 3 tests
- [ ] Write 2 Stage 4 tests
- [ ] Write 2 Stage 5 tests
- [ ] Write 2 Stage 6 tests
- [ ] Write 1 Stage 8 test
- [ ] Write 5+ full pipeline tests
- [ ] Verify all 50+ tests pass

### Error Handling Phase (Week 3-4)

- [ ] Write 10+ error scenario tests
- [ ] Populate remaining fixtures
- [ ] Add docstring examples
- [ ] Create SIMULATOR_USAGE.md guide

---

## Running the Tests

```bash
# Run all simulator tests
pytest tests/simulator/ -v

# Run integration tests only
pytest tests/integration/ -v

# Run specific stage tests
pytest tests/integration/test_stage_0_persist.py -v

# Run with detailed output
pytest tests/integration/test_full_pipeline.py -vv --tb=short

# Run and show which stage failed
pytest tests/integration/test_full_pipeline.py -v --tb=line

# Measure coverage
pytest tests/integration/ --cov=src/aijournal --cov-report=html
```

---

## Success Criteria

✅ **Coverage:**
- All 9 capture stages have dedicated tests
- Full pipeline runs without errors
- 50-60+ new integration tests

✅ **Determinism:**
- All tests use fixed seeds
- All tests use monkeypatched time
- No flaky failures on re-runs

✅ **Clarity:**
- Test names clearly describe what's tested
- Failure messages are actionable
- Diff reports show expected vs actual

✅ **Maintainability:**
- Fixtures are reusable across tests
- Validators can be extended for new schemas
- Reporters produce consistent output

---

## References

- **ARCHITECTURE.md**: System design and data flows
- **AUDIT_TEST_COVERAGE.md**: Complete coverage analysis
- **conftest.py**: Existing fixtures and CLI runner setup
- **tests/helpers.py**: Existing helper functions

---

**Version**: 1.0
**Last Updated**: 2025-11-12
