# Migration Guide: Adopting Tolerant Parsing

This guide provides step-by-step instructions for migrating the capture pipeline to use the new tolerant parsing utilities.

## Quick Start

**New utilities location**: `src/aijournal/services/capture/tolerant.py`

**What's new**:
- `split_frontmatter_tolerant()`: Never-crash front-matter parser
- `parse_date_tolerant()`: Flexible date parser with fallback

**Key benefit**: Handle real-world journal entries with varied formats without pipeline crashes.

## Phase 1: Add Opt-In Support

### 1. Add tolerant flag to capture stages

**In `src/aijournal/services/capture/utils.py`**:

```python
def normalize_markdown(
    markdown_path: Path,
    *,
    root: Path,
    config: AppConfig,
    source_hash: str,
    source_type: str,
    tolerant: bool = False,  # NEW: Add flag
) -> tuple[Path, bool]:
    text = markdown_path.read_text(encoding="utf-8")

    if tolerant:
        from aijournal.services.capture.tolerant import (
            split_frontmatter_tolerant,
            parse_date_tolerant,
        )
        import logging
        logger = logging.getLogger(__name__)

        result = split_frontmatter_tolerant(text)
        for warning in result.warnings:
            logger.warning(f"{markdown_path.name}: {warning}")

        frontmatter = result.data
        body = result.body

        date_result = parse_date_tolerant(
            frontmatter.get("created_at"),
            fallback=time_utils.now()
        )
        for warning in date_result.warnings:
            logger.warning(f"{markdown_path.name}: {warning}")
        created_dt = date_result.dt
    else:
        # Existing strict parsing
        frontmatter, body = split_frontmatter(text)
        created_dt = resolve_created_dt(frontmatter.get("created_at"), time_utils.now())

    # ... rest of function remains unchanged
```

### 2. Add CLI flag to capture command

**In `src/aijournal/cli.py` (or wherever capture command is defined)**:

```python
@app.command()
def capture(
    # ... existing parameters ...
    tolerant: bool = typer.Option(
        False,
        "--tolerant",
        help="Use tolerant parsing for front-matter and dates (never crashes)",
    ),
) -> None:
    """Capture a journal entry."""
    # Pass tolerant flag through to capture stages
    inputs = CaptureInput(..., tolerant=tolerant)
    result = run_capture(inputs)
```

### 3. Thread flag through capture stages

**Update stage signatures to accept and pass through the flag**:

```python
# Stage 0 (persist)
def _persist_file_entry(
    inputs: CaptureInput,
    root: Path,
    config: AppConfig,
    manifest: list[ManifestEntry],
    *,
    source_path: Path | None = None,
    snapshot: bool = True,
    tolerant: bool = False,  # NEW
) -> EntryResult:
    # Use tolerant when calling normalize_markdown
    normalized_path, changed = normalize_markdown(
        markdown_path,
        root=root,
        config=config,
        source_hash=digest,
        source_type=inputs.source_type,
        tolerant=tolerant,  # NEW
    )
```

## Phase 2: Default to Tolerant for File Imports

**Rationale**: External files (imported notes) are most likely to have format variations.

```python
def _persist_file_entry(
    inputs: CaptureInput,
    root: Path,
    config: AppConfig,
    manifest: list[ManifestEntry],
    *,
    source_path: Path | None = None,
    snapshot: bool = True,
) -> EntryResult:
    # Auto-enable tolerant mode for file imports
    tolerant = inputs.source != "stdin"  # or inputs.source == "file"

    normalized_path, changed = normalize_markdown(
        markdown_path,
        root=root,
        config=config,
        source_hash=digest,
        source_type=inputs.source_type,
        tolerant=tolerant,
    )
```

## Phase 3: Make Tolerant the Default

### 1. Change default behavior

```python
@app.command()
def capture(
    # ... existing parameters ...
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Use strict parsing (will crash on malformed input)",
    ),
) -> None:
    """Capture a journal entry."""
    # Invert: tolerant is now default
    inputs = CaptureInput(..., tolerant=not strict)
    result = run_capture(inputs)
```

### 2. Update documentation

Update `docs/workflow.md` and `README.md` to mention that:
- Tolerant parsing is now the default
- Warnings are logged but don't block ingestion
- Use `--strict` if you need validation feedback

### 3. Update tests

Add test cases for tolerant mode in `tests/services/test_capture.py`:

```python
def test_capture_tolerant_mode_handles_malformed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that tolerant mode processes entries with issues."""
    malformed_entry = tmp_path / "malformed.md"
    malformed_entry.write_text(
        "---\nid: test\ncreated_at: not-a-date\n---\nBody",
        encoding="utf-8"
    )

    inputs = CaptureInput(
        source="file",
        paths=[str(malformed_entry)],
        tolerant=True,
    )
    result = run_capture(inputs)

    # Should succeed despite malformed date
    assert result.entries
    assert not result.errors
```

## Rollout Strategy

### Week 1: Opt-In Testing
- Merge tolerant utilities
- Add `--tolerant` flag
- Encourage team to test with real journal imports
- Monitor warnings logs for common issues

### Week 2: File Import Default
- Default to tolerant for `capture --from`
- Keep strict for stdin/text entry
- Collect feedback on warning messages

### Week 3: Full Default
- Make tolerant the default for all capture modes
- Add `--strict` for validation use cases
- Update all documentation

## Backward Compatibility

### No Breaking Changes
- Existing capture calls work unchanged
- Strict parsing available via `--strict`
- All existing tests continue to pass

### New Features Only
- `--tolerant` flag is additive
- Warning logging doesn't break pipelines
- Gradual migration allows testing

## Testing Checklist

Before merging:
- [ ] All existing tests pass (`uv run pytest`)
- [ ] Pre-commit hooks pass (`uv run pre-commit run --all-files`)
- [ ] New tolerant tests pass (`uv run pytest tests/services/test_tolerant.py`)
- [ ] Manual test with real journal files
- [ ] Check warning logs for clarity

After merging:
- [ ] Monitor logs for frequent warnings
- [ ] Gather feedback on warning messages
- [ ] Identify common issues for auto-correction
- [ ] Consider adding format suggestions

## Example: Full Integration

Here's a complete example showing the final integrated state:

**`src/aijournal/services/capture/utils.py`**:
```python
def normalize_markdown(
    markdown_path: Path,
    *,
    root: Path,
    config: AppConfig,
    source_hash: str,
    source_type: str,
    tolerant: bool = True,  # Default True after Phase 3
) -> tuple[Path, bool]:
    import logging
    from aijournal.services.capture.tolerant import (
        split_frontmatter_tolerant,
        parse_date_tolerant,
    )

    logger = logging.getLogger(__name__)
    text = markdown_path.read_text(encoding="utf-8")

    if tolerant:
        result = split_frontmatter_tolerant(text)
        for warning in result.warnings:
            logger.warning(f"Front-matter issue in {markdown_path.name}: {warning}")
        frontmatter = result.data
        body = result.body

        date_result = parse_date_tolerant(
            frontmatter.get("created_at"),
            fallback=time_utils.now()
        )
        for warning in date_result.warnings:
            logger.warning(f"Date issue in {markdown_path.name}: {warning}")
        created_dt = date_result.dt
    else:
        # Strict mode (raises on errors)
        frontmatter, body = split_frontmatter(text)
        created_dt = resolve_created_dt(frontmatter.get("created_at"), time_utils.now())

    created_str = time_utils.format_timestamp(created_dt)
    date_str = created_dt.strftime("%Y-%m-%d")

    # ... rest of normalization logic unchanged
```

## Monitoring and Metrics

After rollout, track:
1. **Warning frequency**: Which warnings occur most often?
2. **Format distribution**: What date/front-matter formats are used?
3. **Error reduction**: How many fewer pipeline crashes?
4. **User feedback**: Are warning messages helpful?

Use this data to:
- Refine warning messages
- Add auto-correction features
- Identify format standards to encourage

## Next Steps

See `docs/tolerant-parsing.md` for:
- Complete API reference
- All supported formats
- Testing guide
- Future enhancement ideas
