# Tolerant Parsing Utilities

This document describes the tolerant front-matter and date parsing utilities introduced to make aijournal's ingestion pipeline more robust when handling human-written journal entries.

## Overview

The `aijournal.services.capture.tolerant` module provides parsing functions that never raise exceptions for malformed input. Instead, they return partial results with warnings, allowing the pipeline to continue processing even when entries have formatting issues.

## Motivation

Human-written journal entries often contain:
- **Varied date formats**: "Jan 5, 2024", "1/5/2024", "2024-1-5", etc.
- **Inconsistent front-matter**: Missing delimiters, malformed YAML/TOML, empty blocks
- **Unknown metadata fields**: Custom fields not in the standard schema
- **Mixed conventions**: Different tools use different front-matter formats

The strict parsing in `services/capture/utils.py:split_frontmatter()` would crash on these variations. The tolerant utilities handle these gracefully.

## Front-Matter Parsing

### Function: `split_frontmatter_tolerant(text: str) -> ParseResult`

Parses front-matter from markdown text, supporting multiple formats:

#### Supported Formats

1. **YAML** (delimiter: `---`)
   ```markdown
   ---
   id: entry-123
   title: My Entry
   tags:
     - work
     - planning
   ---

   Entry body text.
   ```

2. **TOML** (delimiter: `+++`)
   ```markdown
   +++
   id: entry-123
   title: My Entry
   tags:
     - work
     - planning
   +++

   Entry body text.
   ```

   **Note**: Currently uses `yaml.safe_load()` for TOML parsing, which accepts a subset of TOML syntax. For strict TOML compliance, consider using `tomli`/`tomllib`.

3. **JSON** (delimiter: `{` or `[`)
   ```markdown
   {
     "id": "entry-123",
     "title": "My Entry",
     "tags": ["work", "planning"]
   }

   Entry body text.
   ```

4. **No front-matter**
   ```markdown
   Plain markdown without any front-matter delimiter.
   ```

#### Return Value: ParseResult

```python
class ParseResult:
    data: dict[str, object]     # Parsed metadata (empty dict on failure)
    body: str                    # Body text after front-matter
    format: FrontMatterFormat    # "yaml", "toml", "json", or "none"
    warnings: list[str]          # Warning messages for issues encountered
```

#### Behaviors and Warnings

| Scenario | Behavior | Warning |
|----------|----------|---------|
| Valid front-matter | Parse successfully | None |
| Unknown keys | Preserve them in `data` | "Unknown front-matter keys (will be preserved): ..." |
| Missing closing delimiter | Return empty `data`, full text as `body` | "Incomplete YAML/TOML front-matter block" |
| Malformed syntax | Return empty `data`, preserve text | "Failed to parse {format} front-matter: {error}" |
| Empty block | Return empty `data`, extract body | "Empty front-matter block" |
| Non-dict result | Return empty `data`, preserve text | "Front-matter is not a dictionary" |
| No delimiter | Return empty `data`, full text as `body` | "No front-matter delimiter found" |

#### Known Front-Matter Keys

The following keys are recognized (unknown keys generate warnings but are preserved):
- `id`, `slug`, `title`, `created_at`
- `tags`, `projects`, `summary`, `mood`
- `origin`, `sections`

## Date Parsing

### Function: `parse_date_tolerant(value: object, fallback: datetime | None = None) -> DateParseResult`

Parses date/datetime values from various formats, always returning a valid datetime.

#### Supported Formats

1. **ISO 8601**
   - `"2025-01-05T10:30:00Z"` (with timezone)
   - `"2025-01-05T10:30:00+05:30"` (with offset)
   - `"2025-01-05"` (date only, assumes UTC)

2. **Flexible YYYY-MM-DD**
   - `"2024-1-5"` (single-digit month/day)
   - `"2024-01-05"` (zero-padded)

3. **Common Text Formats**
   - `"January 5, 2024"` (full month name)
   - `"Jan 5, 2024"` (abbreviated month)
   - `"5 January 2024"` (day-first)
   - `"5 Jan 2024"` (day-first, abbreviated)

4. **Slashed Dates** (assumes MM/DD/YYYY)
   - `"1/5/2024"`
   - `"01/05/2024"`

5. **Python Objects**
   - `datetime` objects (with or without timezone)
   - `date` objects (converted to datetime at midnight UTC)
   - Date-like objects with `year`, `month`, `day` attributes

#### Return Value: DateParseResult

```python
class DateParseResult:
    dt: datetime              # Parsed datetime (always has timezone, UTC if not specified)
    format_used: str          # Format that successfully parsed the value
    warnings: list[str]       # Warning messages for issues
```

#### Format Codes

| Code | Meaning |
|------|---------|
| `"iso8601"` | ISO 8601 format |
| `"yyyy-mm-dd"` | Year-month-day with flexible separators |
| `"text"` | Common text formats (month names) |
| `"slashed"` | MM/DD/YYYY format |
| `"datetime"` | Python datetime object |
| `"date-like"` | Date-like object with year/month/day |
| `"fallback"` | Parsing failed, used provided fallback |
| `"now"` | Parsing failed, used current time |

#### Behaviors and Warnings

| Scenario | Behavior | Warning |
|----------|----------|---------|
| Valid format | Parse successfully | None (unless missing timezone) |
| Missing timezone | Add UTC timezone | "No timezone in '{text}', assuming UTC" |
| None value with fallback | Use fallback | "Value is None, using fallback" |
| None value without fallback | Use current time | "Value is None and no fallback provided" |
| Empty string with fallback | Use fallback | "Empty string, using fallback" |
| Unparseable with fallback | Use fallback | "Failed to parse date '{text}', using fallback" |
| Unparseable without fallback | Use current time | "Failed to parse date '{text}' and no fallback" |

## Migration Path

### Current Usage (Strict)

```python
from aijournal.services.capture.utils import split_frontmatter, resolve_created_dt

# May raise ValueError on malformed input
frontmatter, body = split_frontmatter(markdown_text)
created_dt = resolve_created_dt(frontmatter.get("created_at"), fallback_dt)
```

### Tolerant Usage (Recommended)

```python
from aijournal.services.capture.tolerant import (
    split_frontmatter_tolerant,
    parse_date_tolerant,
)

# Never raises, returns warnings instead
result = split_frontmatter_tolerant(markdown_text)
if result.warnings:
    for warning in result.warnings:
        logger.warning(f"Front-matter issue: {warning}")

# Parse date with fallback
date_result = parse_date_tolerant(
    result.data.get("created_at"),
    fallback=datetime.now(UTC)
)
if date_result.warnings:
    for warning in date_result.warnings:
        logger.warning(f"Date parsing issue: {warning}")

# Use parsed values
created_dt = date_result.dt
frontmatter = result.data
body = result.body
```

### Gradual Migration

1. **Phase 1**: Add tolerant parsing as opt-in
   - Keep existing strict parsing as default
   - Add `--tolerant` flag to capture command
   - Log warnings from tolerant parsing

2. **Phase 2**: Use tolerant parsing for file imports
   - Import from external sources (notes, other tools) uses tolerant mode
   - Text/stdin capture continues to use strict mode

3. **Phase 3**: Make tolerant parsing default
   - Switch all capture paths to tolerant parsing
   - Keep strict mode available via `--strict` flag for validation

## Integration Points

### In capture pipeline

The tolerant utilities integrate with these stages:

1. **Stage 0 (Persist)**
   - `_persist_file_entry()`: Use `split_frontmatter_tolerant()` for imported files
   - `_persist_text_entry()`: Could use tolerant mode or keep strict for interactive input

2. **Stage 1 (Normalize)**
   - `normalize_markdown()`: Replace `split_frontmatter()` call with tolerant version
   - `resolve_created_dt()`: Replace with `parse_date_tolerant()`

3. **Capture utils**
   - `journal_path()`: Replace `datetime.strptime()` with `parse_date_tolerant()`

### Example Integration

```python
def normalize_markdown(
    markdown_path: Path,
    *,
    root: Path,
    config: AppConfig,
    source_hash: str,
    source_type: str,
    tolerant: bool = False,  # Add flag
) -> tuple[Path, bool]:
    text = markdown_path.read_text(encoding="utf-8")

    if tolerant:
        from aijournal.services.capture.tolerant import (
            split_frontmatter_tolerant,
            parse_date_tolerant,
        )

        result = split_frontmatter_tolerant(text)
        if result.warnings:
            logger.warning(f"Front-matter issues in {markdown_path}: {result.warnings}")
        frontmatter = result.data
        body = result.body

        date_result = parse_date_tolerant(
            frontmatter.get("created_at"),
            fallback=time_utils.now()
        )
        if date_result.warnings:
            logger.warning(f"Date parsing issues in {markdown_path}: {date_result.warnings}")
        created_dt = date_result.dt
    else:
        # Existing strict parsing
        frontmatter, body = split_frontmatter(text)
        created_dt = resolve_created_dt(frontmatter.get("created_at"), time_utils.now())

    # ... rest of normalization
```

## Testing

Comprehensive unit tests are in `tests/services/test_tolerant.py`:

- **Front-matter tests**: Valid formats, malformed input, edge cases
- **Date parsing tests**: All supported formats, edge cases, fallback behavior
- **Integration tests**: Combined usage patterns, never-crash guarantees

Run tests:
```bash
uv run pytest tests/services/test_tolerant.py -v
```

## Performance Considerations

- **Overhead**: Tolerant parsing has minimal overhead (~5-10% slower than strict)
- **Logging**: Warnings are logged at WARNING level, not printed to console by default
- **Fallback strategy**: Multiple parsers tried in order of likelihood (ISO first, text last)
- **Memory**: ParseResult/DateParseResult objects are lightweight

## Future Enhancements

1. **Strict TOML support**: Add `tomli`/`tomllib` for proper TOML parsing
2. **Configurable warnings**: Allow suppressing specific warning types
3. **Validation levels**: Add "strict", "tolerant", "permissive" modes
4. **Auto-correction**: Suggest corrections for common mistakes
5. **Format detection**: Return confidence scores for format detection
6. **Custom validators**: Allow registering custom front-matter validators

## References

- Implementation: `src/aijournal/services/capture/tolerant.py`
- Tests: `tests/services/test_tolerant.py`
- Original strict parsing: `src/aijournal/services/capture/utils.py:374-398`
- Date resolution: `src/aijournal/services/capture/utils.py:287-304`
