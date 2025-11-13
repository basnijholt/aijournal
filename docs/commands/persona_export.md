# `aijournal persona export`

`aijournal persona export` renders the current self-model into deterministic, LLM-ready Markdown persona cards. The command reads `derived/persona/persona_core.yaml`, trims content to chosen token budgets, and prints the result to stdout by default (or writes to files with `--output` / `--output-dir`).

## Examples

```bash
# Tiny preset (~200 tokens) printed to stdout
uv run aijournal persona export --variant tiny

# Short preset (~600 tokens) with explicit token override
uv run aijournal persona export --tokens 900 --deterministic --seed 42

# Tiny + short cards in a single run (stdout, separated by markers)
uv run aijournal persona export --variant tiny --variant short

# Write one file per variant under a directory (includes all presets)
uv run aijournal persona export --variant all --output-dir derived/persona/cards --overwrite

# Full preset saved to a specific file; overwrite on rerun
uv run aijournal persona export --variant full --output derived/persona/card.md --overwrite

# Custom ordering that favors recent claims and suppresses claim markers
uv run aijournal persona export --sort recency --max-items 5 --no-claim-markers
```

## Flags

| Flag | Description |
| --- | --- |
| `--variant {tiny,short,full,all}` | Repeat to render multiple presets; defaults to `short` (~600 tokens). |
| `--tokens INT` | Custom token budget override (positive integer). |
| `--output PATH` | Write Markdown to a file instead of stdout. |
| `--output-dir PATH` | Directory that receives one Markdown file per requested variant. |
| `--overwrite` | Allow replacing an existing `--output` file. |
| `--deterministic/--no-deterministic` | Stable ordering by default; disable for stochastic tie-breakers. |
| `--seed INT` | Optional seed used for deterministic tie-breaking. |
| `--sort {strength,recency,id}` | Claim ordering strategy. |
| `--max-items INT` | Cap the number of claims included in the export. |
| `--no-claim-markers` | Omit `[claim:<id>]` markers from claim bullets. |

## Behavior

- Always includes sections in this order: Identity & Roles, Core Values, Constraints & Boundaries, Preferences for AI assistants, optional Goals / Work / Habits, Claims Snapshot, and a closing “Instructions for the assistant” block.
- Honors token budgets using the configured `token_estimator.char_per_token` ratio. Higher-priority sections stay intact longer during trimming; optional sections and claims are dropped first when over budget.
- Claim bullets include `[claim:<id>]` markers (unless disabled) so downstream prompts can reference specific atoms.
- When multiple variants are requested, stdout renders each block with lightweight `<!-- persona:... -->` markers and separates them with `---`. Use `--output-dir` to capture each variant as a standalone file.
- Requires a fresh `persona_core.yaml`; run `uv run aijournal persona build` if the export reports a missing or stale artifact.

Use the exported Markdown as a drop-in prompt primer for other LLM sessions or tooling.
