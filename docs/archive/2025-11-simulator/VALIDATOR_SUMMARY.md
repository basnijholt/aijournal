# Stage Validators Implementation Summary

## Overview

Implemented comprehensive validators for pipeline stages 3–8, extending the existing human simulator to validate all capture pipeline stages from persist (0) through pack export (8).

## New Validators

### Stage 3: Facts Extraction (`Stage3Validator`)
**Artifacts**: `derived/microfacts/<date>.yaml`

**Validations**:
- ✅ Micro-facts files exist for changed dates
- ✅ MicroFactsFile artifacts load correctly
- ✅ Fact statements are concise (<500 chars)
- ✅ Fact evidence references valid entry IDs from manifest

### Stage 4: Profile Suggestions (`Stage4Validator`)
**Artifacts**: `derived/profile_proposals/<date>.yaml`

**Validations**:
- ✅ Profile proposal files exist for changed dates
- ✅ ProfileUpdateProposals artifacts load correctly
- ⚠️ Missing proposals generate warnings (not errors)

### Stage 5: Characterization (`Stage5Validator`)
**Artifacts**: `derived/pending/profile_updates/*.yaml`, `derived/pending/profile_updates/applied/*.yaml`

**Validations**:
- ✅ Characterization batch files load as artifact envelopes
- ✅ ProfileUpdateBatch schema validation
- ✅ Batch IDs are present and non-empty
- ✅ Handles both pending and applied batches

### Stage 6: Index Refresh (`Stage6Validator`)
**Artifacts**: `derived/index/index.db`, `derived/index/annoy.index`, `derived/index/meta.json`

**Validations**:
- ⚠️ Index database exists
- ⚠️ Annoy vector index exists
- ⚠️ Index metadata file exists
- Note: All missing files generate warnings since index rebuild is optional

### Stage 7: Persona Build (`Stage7Validator`)
**Artifacts**: `derived/persona/persona_core.yaml`

**Validations**:
- ⚠️ Persona core file exists
- ✅ PersonaCore artifact loads correctly
- ⚠️ Persona has content (profile or claims)
- Note: Missing persona generates warnings, invalid structure is an error

### Stage 8: Pack Export (`Stage8Validator`)
**Artifacts**: `derived/packs/*.yaml`, `derived/packs/*.json`

**Validations**:
- ✅ Pack files load as valid YAML or JSON
- ✅ YAML packs validate against PackBundle schema
- ✅ JSON packs are valid JSON objects
- ⚠️ Missing packs are warnings (pack generation is optional)

## Orchestrator Updates

### `HumanSimulator` Class
- Extended `max_stage` parameter from 2 to support 0–8
- Validation checks now run for all enabled stages
- Maintains deterministic fixture generation
- Uses fake LLM/embeddings for reproducible tests

### CLI Integration
```bash
# Run simulator with all stages
aijournal ops dev human-sim --max-stage 8

# Run only through facts extraction
aijournal ops dev human-sim --max-stage 3

# Keep workspace for inspection
aijournal ops dev human-sim --max-stage 5 --output /tmp/inspect --keep-workspace
```

## Test Coverage

### Integration Tests (`tests/test_cli_simulator.py`)

1. **Parametrized stage tests**: Tests each stage from 0–8 individually
2. **Direct API tests**: Validates Python API usage
3. **Validation failure tests**: Ensures validators catch real issues

### Test Results
```
261 passed, 10 warnings in 6.65s
```

All tests pass with deterministic fake LLM behavior enabled via `AIJOURNAL_FAKE_OLLAMA=1`.

## Design Principles

### Severity Levels
- **Error**: Critical failures that prevent downstream operations (e.g., invalid artifact schema)
- **Warning**: Expected absences in certain contexts (e.g., optional index rebuild)

### Validation Strategy
- Load artifacts using proper loaders (`load_artifact_data` for envelopes, `load_yaml_model` for raw YAML)
- Validate against domain models from `aijournal.domain.*`
- Check referential integrity (e.g., facts reference valid entry IDs)
- Verify idempotency constraints (e.g., profile suggestions written once per date)

### Error Handling
- Broad exception catching with `# noqa: BLE001` since validators diagnose issues
- Clear error messages with file paths and dates
- Preserve all validation context for debugging

## Validator Registry

The `StageValidatorRegistry` now includes all 9 validators (0–8):
- Stage 0: Persist (manifest, markdown files)
- Stage 1: Normalize (normalized YAML entries)
- Stage 2: Summarize (daily summaries)
- Stage 3: Facts (micro-facts extraction)
- Stage 4: Profile (profile suggestions)
- Stage 5: Characterize (profile update batches)
- Stage 6: Index (search index rebuild)
- Stage 7: Persona (persona core generation)
- Stage 8: Pack (context pack export)

## Usage Examples

### CLI Testing
```bash
# Quick validation through normalization
AIJOURNAL_FAKE_OLLAMA=1 aijournal ops dev human-sim --max-stage 2

# Full pipeline validation
AIJOURNAL_FAKE_OLLAMA=1 aijournal ops dev human-sim --max-stage 8 --keep-workspace
```

### Python API
```python
from aijournal.simulator.orchestrator import HumanSimulator

simulator = HumanSimulator(max_stage=5)
report = simulator.run(keep_workspace=True)

print(f"Validation: {'✅ ok' if report.validation.ok else '❌ failed'}")
print(f"Stages: {report.capture_result.stages_completed}")
print(f"Errors: {len(report.validation.errors())}")
```

### Validator Inspection
```python
from aijournal.simulator.validators import (
    StageValidatorRegistry,
    ValidatorContext,
    render_failures_compact,
)

registry = StageValidatorRegistry()
ctx = ValidatorContext(workspace=path, capture=result)
validation = registry.run(ctx, stages=[3, 4, 5])

if not validation.ok:
    print(render_failures_compact(validation.failures))
```

## Implementation Files

- `src/aijournal/simulator/validators.py`: All 9 stage validators
- `src/aijournal/simulator/orchestrator.py`: HumanSimulator with extended stage support
- `src/aijournal/cli.py`: CLI command with `--max-stage` flag
- `tests/test_cli_simulator.py`: Comprehensive integration tests

## Future Enhancements

1. **Parallel validation**: Run independent validators concurrently
2. **Incremental validation**: Validate only changed artifacts
3. **Custom validator plugins**: Allow workspace-specific validation rules
4. **Validator metrics**: Track validation performance and coverage
5. **Repair suggestions**: Auto-fix common validation failures
