# Characterize Step Redesign

## 0. Problem Statement

Characterize currently forces the LLM to emit a deeply nested schema (`ClaimProposal` → `ClaimAtomInput`) filled with system-only metadata such as scope, strength, and provenance. We compensate by asking the model for a “light” response and then running a 150+ line transformer to rebuild the strict version, yet the saved artifacts still expose that strict structure. This two-schema dance makes debugging and review harder than necessary.

## 1. Goal Recap

The characterize stage must output a **single, human-reviewable artifact** that downstream tooling can apply after optional review. Because there are no external users or legacy data, we can redefine the schema from scratch with zero backward-compatibility code. The new design should:

- Let the LLM emit only the fields it truly understands (type, statement, evidence reference, reason).
- Push all policy defaults (scope, strength, status, provenance) into deterministic Python after validation—not into the prompt.
- Persist in exactly the same structure the LLM produces (plus validator-enforced defaults) so there is no hidden “strict” schema.

## 2. Canonical Schema (Flattened Proposals)

We keep the `ProfileUpdateProposals` container but flatten each claim/facet entry so the LLM deals with a shallow, stable schema. **Persisted artifacts will only contain these user-facing fields**; system-only metadata is derived in-memory and excluded at serialization time.

### ClaimProposal (new shape)
- `type`: ClaimType enum.
- `statement`: ≤160 chars.
- `subject`: optional, defaults to `"self"`.
- `predicate`: optional, defaults to `"states"`.
- `value`: optional, defaults to `statement`.
- `reason`: optional ≤25 words, becomes the stored rationale.
- `evidence_entry`: normalized entry id (required when grounded).
- `evidence_para`: optional int ≥0 (defaults to 0).

System-only fields (`scope`, `strength`, `status`, `method`, `user_verified`, `review_after_days`, structured `evidence`, `normalized_ids`, `manifest_hashes`) are derived inside `ClaimProposal` via validators and marked `Field(..., exclude=True)`. Pydantic respects `exclude=True` for `model_dump`, so prompts and persisted YAML automatically hide those fields while runtime objects still expose them.

### FacetChange (flattened)
- `path`: dotted path in `SelfProfile`.
- `action`: `set` or `remove`.
- `value`: required when `action == set` (string or list of strings).
- `reason`, `evidence_entry`, `evidence_para` as above.
- Derived metadata (confidence defaults, structured evidence, etc.) handled exactly like claims.

### Interview Prompts
- Remain `list[str]`, ≤20 words. No other changes required.

## 3. Downstream Flow

1. The LLM emits flattened `ProfileUpdateProposals` (claims/facets/interview prompts).
2. Pydantic validators immediately inject policy defaults and structured evidence, so every consumer still receives a fully populated `ClaimProposal`/`FacetChange` at runtime.
3. Because `Field(..., exclude=True)` suppresses derived metadata during serialization, the existing `save_artifact()` call (`model_dump(...)`) already writes only user-facing fields—no custom serializer needed.
4. `profile apply`, persona builders, packs, etc., require only minor attribute access updates (e.g., `proposal.statement` instead of `proposal.claim.statement`).

## 4. Implementation Plan

| Area | Action |
| --- | --- |
| `src/aijournal/domain/changes.py` | Redefine `ClaimProposal`/`FacetChange` with flattened user-facing fields, derived system fields, and `Field(..., exclude=True)` for anything not authored by the LLM. Remove nested `ClaimAtomInput` use. |
| `prompts/characterize.md` | Update schema section/examples to show only flattened fields (no strict template). Emphasize `evidence_entry`/`evidence_para` instead of `normalized_ids`/`evidence`. |
| `src/aijournal/pipelines/characterize.py`, `commands/characterize.py` | Delete the light→strict transformer; structured call now returns the canonical schema directly. |
| `src/aijournal/commands/profile.py`, CLI previews, services | Update attribute accesses and ensure conversion to `ClaimAtom` uses the derived fields (already present after validation). |
| Tests | Refresh fixtures/assertions in `tests/pipelines/test_characterize.py`, `tests/pipelines/test_facts.py`, CLI tests, prompt examples, etc., to match the new flattened shape. |
| Docs / Schemas | Regenerate JSON schemas, update `ARCHITECTURE.md`, `README`, `CHANGELOG`, and prompt docs to describe the simplified contract. |

## 5. Notes

- No migration helpers or dual-read logic—old schema is removed outright.
- Delete `characterize_light.py` and related tests once the flattened models are in place.
- Apply the same flattening strategy to `profile suggest` so both commands share the identical proposal models.
