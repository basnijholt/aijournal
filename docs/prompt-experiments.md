# A/B/N Prompt Experimentation Design

## Overview

Lightweight mechanism for testing multiple prompt variants within the existing aijournal architecture. Focuses on simplicity, minimal code changes, and seamless integration with the current prompt loading and metadata tracking infrastructure.

---

## Core Principles

1. **Minimal Surface Area**: Extend existing prompt loading logic; avoid new abstractions
2. **Config-Driven**: Variants defined in `config.yaml` with simple override syntax
3. **Transparent Metadata**: Experiment metadata flows through existing `LLMResult` and artifact system
4. **Zero Breaking Changes**: Falls back gracefully when no experiment is active
5. **Analysis-Ready**: Structured logging enables downstream analysis without custom tooling

---

## Design

### 1. Variant Representation

**Location**: Prompt variants live in the filesystem alongside base prompts:

```
prompts/
  summarize_day.md              # Base/control variant
  summarize_day.variant-a.md    # Experimental variant A
  summarize_day.variant-b.md    # Experimental variant B
  extract_facts.md
  extract_facts.variant-concise.md
```

**Naming Convention**: `{base_name}.variant-{label}.md`

**Why filesystem?**
- Leverages existing `resolve_prompt_path()` infrastructure
- Git-friendly (diffs, history, branches)
- No new parsers or data structures
- Easy manual inspection and editing

### 2. Experiment Configuration

**Location**: `config.yaml` gains a new top-level `experiments` section:

```yaml
model: "gpt-oss:20b"
# ... existing config ...

experiments:
  # Experiment definition
  summarize_day:
    enabled: true
    strategy: "round_robin"  # or "random", "weighted", "fixed"
    variants:
      - label: "control"     # Uses base prompts/summarize_day.md
        weight: 1.0
      - label: "variant-a"   # Uses prompts/summarize_day.variant-a.md
        weight: 1.0
      - label: "variant-b"
        weight: 2.0          # 2x probability vs others (weighted strategy only)

  extract_facts:
    enabled: false           # Inactive experiment
    strategy: "fixed"
    fixed_variant: "variant-concise"
```

**Schema Addition** (`src/aijournal/common/app_config.py`):

```python
class VariantConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    weight: float = 1.0

class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    strategy: Literal["round_robin", "random", "weighted", "fixed"] = "round_robin"
    variants: list[VariantConfig] = Field(default_factory=list)
    fixed_variant: str | None = None  # Used when strategy="fixed"

class AppConfig(BaseModel):
    # ... existing fields ...
    experiments: dict[str, ExperimentConfig] = Field(default_factory=dict)
```

**Strategy Semantics**:
- `round_robin`: Cycle through variants in order (stateful, uses workspace counter)
- `random`: Uniform random selection per run
- `weighted`: Weighted random sampling based on `weight` field
- `fixed`: Always use `fixed_variant` (useful for manual testing or overrides)

### 3. Variant Selection Logic

**Extension Point**: Modify `_load_prompt_template()` in `src/aijournal/commands/summarize.py`:

```python
def _select_variant(
    base_path: str,
    config: AppConfig,
    *,
    workspace: Path | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Select a prompt variant based on active experiments.

    Returns:
        (resolved_path, variant_label, experiment_metadata)
    """
    # Extract base name (e.g., "prompts/summarize_day.md" -> "summarize_day")
    base_name = Path(base_path).stem

    # Check if experiment is active
    exp_config = config.experiments.get(base_name)
    if not exp_config or not exp_config.enabled or not exp_config.variants:
        return base_path, "control", {}

    # Select variant based on strategy
    if exp_config.strategy == "fixed":
        variant_label = exp_config.fixed_variant or "control"
    elif exp_config.strategy == "round_robin":
        variant_label = _round_robin_select(base_name, exp_config, workspace)
    elif exp_config.strategy == "random":
        variant_label = random.choice([v.label for v in exp_config.variants])
    elif exp_config.strategy == "weighted":
        variant_label = random.choices(
            [v.label for v in exp_config.variants],
            weights=[v.weight for v in exp_config.variants],
            k=1,
        )[0]
    else:
        variant_label = "control"

    # Build variant path
    if variant_label == "control":
        variant_path = base_path
    else:
        base_dir = Path(base_path).parent
        base_stem = Path(base_path).stem
        variant_path = str(base_dir / f"{base_stem}.variant-{variant_label}.md")

    # Metadata for logging
    metadata = {
        "experiment": base_name,
        "variant": variant_label,
        "strategy": exp_config.strategy,
    }

    return variant_path, variant_label, metadata

def _round_robin_select(
    base_name: str,
    exp_config: ExperimentConfig,
    workspace: Path | None,
) -> str:
    """Round-robin variant selection with persistent state."""
    if not workspace:
        # Fallback to random if workspace unavailable
        return random.choice([v.label for v in exp_config.variants])

    # Counter file: derived/experiments/{base_name}.counter
    counter_dir = workspace / "derived" / "experiments"
    counter_dir.mkdir(parents=True, exist_ok=True)
    counter_file = counter_dir / f"{base_name}.counter"

    # Read current index
    if counter_file.exists():
        current_idx = int(counter_file.read_text().strip())
    else:
        current_idx = 0

    # Select variant
    variant = exp_config.variants[current_idx % len(exp_config.variants)]

    # Increment and persist
    next_idx = (current_idx + 1) % len(exp_config.variants)
    counter_file.write_text(str(next_idx))

    return variant.label
```

**Integration**: Update `_load_prompt_template()`:

```python
def _load_prompt_template(
    prompt_path: str,
    *,
    config: AppConfig | None = None,
    workspace: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    """Load prompt template, optionally selecting experimental variant.

    Returns:
        (template_text, experiment_metadata)
    """
    # Select variant if experiments active
    if config:
        resolved_path, variant_label, exp_meta = _select_variant(
            prompt_path, config, workspace=workspace
        )
    else:
        resolved_path = prompt_path
        exp_meta = {}

    # Load template (existing logic)
    path = resolve_prompt_path(resolved_path)
    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        key = Path(resolved_path).name
        content = DEFAULT_PROMPTS.get(resolved_path) or DEFAULT_PROMPTS.get(key, "")

    return content, exp_meta
```

### 4. Metadata Flow

**Extension**: Add experiment metadata to `LLMResult` and artifact metadata:

```python
# In _invoke_structured_llm()
template, exp_metadata = _load_prompt_template(
    prompt_path,
    config=config,
    workspace=workspace,
)
prompt = Template(template).safe_substitute(**variables)

# Pass experiment metadata to run_ollama_agent
result: LLMResult[BaseModel] = run_ollama_agent(
    ollama_config,
    prompt,
    # ... existing params ...
    experiment_metadata=exp_metadata,  # NEW
)
```

**Schema Update** (`src/aijournal/common/meta.py`):

```python
@dataclass(frozen=True)
class LLMResult(Generic[_T]):
    payload: _T
    model_name: str
    prompt_path: str
    prompt_hash: str
    created_at: str
    experiment: dict[str, Any] | None = None  # NEW: {experiment, variant, strategy}
```

**Artifact Metadata**: Existing `LLMMeta` blocks gain experiment fields:

```yaml
# derived/summaries/2025-11-14.yaml
kind: daily_summary
meta:
  llm_model: "gpt-oss:20b"
  prompt_path: "prompts/summarize_day.md"
  prompt_hash: "abc123..."
  created_at: "2025-11-14T10:30:00Z"
  experiment:                        # NEW
    experiment: "summarize_day"
    variant: "variant-a"
    strategy: "round_robin"
data:
  # ... summary data ...
```

### 5. Logging & Analysis

**Structured Logging**: Experiment metadata flows into `derived/logs/run_trace.jsonl` via existing `StructuredLogger`:

```json
{
  "timestamp": "2025-11-14T10:30:05Z",
  "event": "llm_call",
  "stage": "summarize",
  "model": "gpt-oss:20b",
  "prompt_path": "prompts/summarize_day.variant-a.md",
  "prompt_hash": "xyz789...",
  "experiment": {
    "experiment": "summarize_day",
    "variant": "variant-a",
    "strategy": "round_robin"
  },
  "duration_ms": 1234,
  "success": true
}
```

**Analysis Workflow**:

1. **Extract experiment runs**:
   ```bash
   jq 'select(.experiment != null)' derived/logs/run_trace.jsonl > experiments.jsonl
   ```

2. **Compare variants** (Python/pandas):
   ```python
   import pandas as pd

   df = pd.read_json("experiments.jsonl", lines=True)

   # Group by experiment + variant
   summary = df.groupby(["experiment.experiment", "experiment.variant"]).agg({
       "duration_ms": ["mean", "median", "std"],
       "success": "mean"
   })
   ```

3. **Link to artifacts**: Match `created_at` timestamps to find outputs for each variant

**No Custom Tooling Required**: Standard Unix tools + pandas/jq handle analysis

---

## CLI Overrides

**Environment Variable**: Force a specific variant without editing config:

```bash
export AIJOURNAL_EXPERIMENT_OVERRIDE="summarize_day:variant-b"
uv run aijournal capture --text "Test entry"
```

**Implementation** (in `_select_variant()`):

```python
override = os.getenv("AIJOURNAL_EXPERIMENT_OVERRIDE")
if override:
    exp_name, variant_label = override.split(":", 1)
    if exp_name == base_name:
        return _build_variant_path(base_path, variant_label), variant_label, {...}
```

---

## Migration Path

1. **Phase 1: Core Infrastructure**
   - Add `ExperimentConfig` to `AppConfig`
   - Extend `_load_prompt_template()` to support variant selection
   - Add experiment metadata to `LLMResult` and artifacts

2. **Phase 2: First Experiment**
   - Create `prompts/summarize_day.variant-concise.md`
   - Enable experiment in test workspace `config.yaml`
   - Run comparison: control vs variant-concise

3. **Phase 3: Analysis Tooling** (optional)
   - Add `aijournal ops experiments report` command
   - Automate variant comparison from structured logs

---

## Example Usage

### Setup Experiment

```bash
# 1. Create variant prompt
cp prompts/summarize_day.md prompts/summarize_day.variant-concise.md
# Edit variant-concise.md to test more concise instructions

# 2. Configure experiment
cat >> config.yaml <<EOF
experiments:
  summarize_day:
    enabled: true
    strategy: "weighted"
    variants:
      - label: "control"
        weight: 1.0
      - label: "variant-concise"
        weight: 2.0  # Test new variant 2x more often
EOF
```

### Run Workflow

```bash
# Capture entries (variant auto-selected per run)
uv run aijournal capture --text "Morning planning session" --tag focus
uv run aijournal capture --text "Afternoon code review" --tag work

# Check which variant was used
cat derived/summaries/2025-11-14.yaml | yq '.meta.experiment'
```

### Analyze Results

```bash
# Extract experiment runs from logs
jq 'select(.experiment.experiment == "summarize_day")' \
   derived/logs/run_trace.jsonl | \
   jq -s 'group_by(.experiment.variant) |
          map({variant: .[0].experiment.variant,
               count: length,
               avg_duration: (map(.duration_ms) | add / length)})'
```

---

## Testing Strategy

**Unit Tests**:
- `test_select_variant_round_robin()`: Verify counter persistence
- `test_select_variant_weighted()`: Distribution matches weights (statistical)
- `test_select_variant_disabled()`: Falls back to control
- `test_experiment_metadata_flow()`: End-to-end metadata propagation

**Integration Tests**:
- Add variant prompt to test fixtures
- Run capture pipeline with experiment enabled
- Assert artifact metadata contains experiment fields

---

## Future Extensions (Out of Scope)

- **Multi-armed bandits**: Adaptive weights based on success metrics
- **Stratified sampling**: Balance variants across date ranges or tags
- **Web UI**: Visual experiment dashboard (stretch goal)

These can build on the same core infrastructure without breaking changes.

---

## Summary

**What Changes**:
1. `AppConfig` gains `experiments: dict[str, ExperimentConfig]` field
2. `_load_prompt_template()` returns `(template, experiment_metadata)`
3. `LLMResult` and artifact `meta` blocks include optional `experiment` field
4. Filesystem: Variant prompts coexist with base prompts using `.variant-{label}.md` suffix

**What Stays the Same**:
- Prompt rendering, validation, and hashing logic unchanged
- Artifact storage format backward-compatible (experiment field is optional)
- CLI commands require no new flags (experiments are config-driven)
- Existing tests continue to pass (experiments disabled by default)

**Estimated Effort**:
- Core implementation: ~200 LOC across 3 files
- Tests: ~150 LOC
- Documentation updates: This file + inline docstrings
- **Total**: ~1 day for experienced contributor familiar with the codebase
