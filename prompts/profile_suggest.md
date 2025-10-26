You maintain a personal self-profile composed of claims and structured facets. Using the
normalized entries plus the current profile, propose JSON suggestions using a single array
named `suggestions`. Each suggestion must take one of two forms:

```
{
  "suggestions": [
    {
      "kind": "claim",
      "id": "optional-kebab-id",
      "statement": "Evidence-backed statement",
      "rationale": "Short justification (≤20 words)",
      "evidence": ["normalized-entry-id"],
      "status": "accepted" | "tentative",
      "confidence": 0.0-1.0
    },
    {
      "kind": "facet",
      "facet_path": "coaching_prefs.check_ins.cadence",
      "value": <JSON-compatible value>,
      "rationale": "Short justification (≤20 words)",
      "evidence": ["normalized-entry-id"]
    }
  ]
}
```

Guidelines:
- Only include fields relevant to the suggestion type (`statement` for claims, `facet_path` and `value` for facets).
- Use the supplied entries for grounding; omit items when support is weak.
- IDs are optional; provide them only when a stable slug already exists.
- Keep rationales brief and factual, and reference evidence IDs where possible.
- Return **only** the JSON payload shown above. No markdown fences or commentary.

DATE: $date

ENTRIES_JSON:
$entries_json

PROFILE_JSON:
$profile_json

CLAIMS_JSON:
$claims_json
