# CLI Migration Guide

The refactor consolidates everyday commands at the top level and moves specialist tools under
`aijournal ops ...`. Use this table to map legacy verbs to their new homes.

| Legacy Command | Replacement |
| -------------- | ----------- |
| `aijournal ingest` | `aijournal capture --from <path> ...` (everyday) or `aijournal ops pipeline ingest` (advanced) |
| `aijournal new` | `aijournal capture --text/--edit ...` |
| `aijournal facts` | `aijournal ops pipeline extract-facts` |
| `aijournal summarize` | `aijournal ops pipeline summarize` |
| `aijournal review-updates` | `aijournal ops pipeline review` |
| `aijournal characterize` | `aijournal ops pipeline characterize` |
| `aijournal profile suggest` | (unchanged) `aijournal ops profile suggest` |
| `aijournal profile apply` | (unchanged) `aijournal ops profile apply` — usually run automatically by `capture` |
| `aijournal profile status` | `aijournal status` (summary) or `aijournal ops profile status` (detailed) |
| `aijournal tail` | `aijournal ops index update` |
| `aijournal pack` | `aijournal export pack` |
| `aijournal chatd` | `aijournal serve chat` |

## Everyday Flow

```sh
uv run aijournal init --path ~/journal
cd ~/journal
uv run aijournal capture --text "What I learned today" --tag reflection
uv run aijournal status
uv run aijournal chat "What progress did I make?"
uv run aijournal export pack --level L1 --format yaml
```

## Advanced Pipelines

Manual reruns remain available under `aijournal ops pipeline ...`. For example:

```sh
# Re-run extraction on a specific day
uv run aijournal ops pipeline extract-facts --date 2025-02-05 --retries 2 --progress

# Ingest a directory in CI without refreshing downstream artifacts
uv run aijournal ops pipeline ingest docs/notes --source-type notes --no-snapshot
```

All `ops` commands accept the same options they did previously; the refactor only reorganizes where
you invoke them.
