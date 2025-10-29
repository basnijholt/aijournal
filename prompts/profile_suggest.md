You maintain a personal self-profile composed of structured claims and facets. Using the
normalized entries plus the current profile, propose grounded updates that match the
`ProfileUpdateProposals` schema below. Output must be a single JSON object with exactly the
keys `claims` and `facets`—no narration or markdown fences.

```
{
  "claims": [
    {
      "claim": {
        "type": "goal",
        "subject": "who or what the claim refers to",
        "predicate": "relationship or attribute",
        "value": "normalized value",
        "statement": "Readable sentence",
        "scope": {"domain": "optional", "context": ["tags"], "conditions": []},
        "strength": 0.0-1.0,
        "status": "accepted" | "tentative" | "rejected",
        "method": "self_report" | "inferred" | "behavioral",
        "user_verified": false,
        "review_after_days": integer
      },
      "normalized_ids": ["normalized-entry-id"],
      "evidence": [
        {"entry_id": "normalized-entry-id", "spans": [{"type": "paragraph", "index": 0}]}
      ],
      "manifest_hashes": ["optional-manifest-hash"],
      "rationale": "≤25 word justification"
    }
  ],
  "facets": [
    {
      "path": "values_motivations.recurring_theme",
      "operation": "set" | "merge" | "remove",
      "value": <JSON-compatible value when operation is set/merge>,
      "method": "inferred" | "self_report" | "behavioral",
      "confidence": 0.0-1.0,
      "review_after_days": integer,
      "user_verified": false,
      "evidence": [
        {"entry_id": "normalized-entry-id", "spans": [{"type": "paragraph", "index": 1}]}
      ],
      "rationale": "≤25 word justification"
    }
  ]
}
```

Guidelines:
- Mine `summary`, `sections`, and `tags` to justify every update; omit proposals when support is weak.
- Prefer refining existing profile elements before introducing new claims or facets.
- Keep `rationale` concise and reference which evidence entry supports the change.
- Use `operation: "remove"` only when evidence strongly contradicts an existing facet.
- Return **only** the JSON payload. No markdown fences or commentary.
- If no grounded updates exist, return `{ "claims": [], "facets": [] }`.

If you cannot produce a valid payload matching this schema, respond with `{"claims": [], "facets": []}` as the entire output.
See `prompts/examples/profile_suggest.json` for a minimal compliant example.

DATE: $date

ENTRIES_JSON:
$entries_json

PROFILE_JSON:
$profile_json

CLAIMS_JSON:
$claims_json
