# CLI Migration Guide

The Phase 1 CLI re-organization moves low-level tools under structured Typer
sub-apps while reserving everyday commands at the top level (`init`, `capture`,
`chat`, `advise`, `status`, `serve chat`, `export pack`). Use the table below to
translate legacy commands to their new paths.

| Legacy command                    | Replacement                                |
| --------------------------------- | ------------------------------------------ |
| `aijournal normalize`             | `aijournal ops pipeline normalize`         |
| `aijournal summarize`             | `aijournal ops pipeline summarize`         |
| `aijournal facts`                 | `aijournal ops pipeline extract-facts`     |
| `aijournal characterize`          | `aijournal ops pipeline characterize`      |
| `aijournal review-updates`        | `aijournal ops pipeline review`            |
| `aijournal ingest …`              | `aijournal ops pipeline ingest …`          |
| `aijournal new …`                 | `aijournal ops dev new …`                  |
| `aijournal profile suggest …`     | `aijournal ops profile suggest …`          |
| `aijournal profile apply …`       | `aijournal ops profile apply …`            |
| `aijournal profile status`        | `aijournal ops profile status`             |
| `aijournal interview`             | `aijournal ops profile interview`          |
| `aijournal persona build`         | `aijournal ops persona build`              |
| `aijournal persona status`        | `aijournal ops persona status`             |
| `aijournal index rebuild`         | `aijournal ops index rebuild`              |
| `aijournal index tail`            | `aijournal ops index update`               |
| `aijournal index search …`        | `aijournal ops index search …`             |
| `aijournal pack …`                | `aijournal export pack …`                  |
| `aijournal chatd`                 | `aijournal serve chat`                     |
| `aijournal feedback-apply`        | `aijournal ops feedback apply`             |
| `aijournal ollama health`         | `aijournal ops system ollama health`       |

Everyday commands stay put: `aijournal init`, `aijournal chat`, and `aijournal
advise` remain unchanged. `aijournal capture`/`status` are currently
placeholders that exit with code 2 until their implementations land.
