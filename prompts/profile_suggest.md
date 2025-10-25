You maintain a personal self-profile composed of claims and structured facets. Using the
normalized entries plus the current profile, propose JSON suggestions with two arrays:

```
{
  "upserts": [
    {
      "target": "claims",
      "operation": "upsert",
      "value": {
        "id": "kebab-case",
        "statement": "Evidence-backed statement",
        "status": "tentative"|"accepted",
        "confidence": 0.0-1.0,
        "sources": [{"entry_id": "..."}],
        "method": "inferred"|"self_report",
        "user_verified": false,
        "review_after_days": 30-180
      },
      "rationale": "One-sentence justification"
    }
  ],
  "updates": [
    {
      "target": "facet.path",
      "operation": "set",
      "value": <JSON-compatible value>,
      "method": "inferred",
      "user_verified": false,
      "evidence": ["entry_id or manifest hash"],
      "rationale": "Short reason"
    }
  ]
}
```

Principles:
- Only suggest changes that are directly supported by the supplied entries.
- Prefer reinforcing or adjusting existing facets before adding brand-new ones.
- Keep rationale concise (≤20 words).
- Leave arrays empty when no grounded updates are available.
- Return **only** the JSON payload above.

DATE: $date

ENTRIES_JSON:
$entries_json

PROFILE_JSON:
$profile_json

CLAIMS_JSON:
$claims_json
