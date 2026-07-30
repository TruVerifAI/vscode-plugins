# Example: synthesize was noise; notes required

## Setup

You ran `mcp__truverifai__synthesize_coding` with the question "What's the standard way to handle date parsing in a TypeScript API?" The response talked about date-fns vs. luxon, JSON serialization patterns, and timezone handling — none of which were relevant to your actual narrow question (was `Date.parse()` adequate for ISO-8601 strings?).

You proceeded with `Date.parse()` as originally planned.

## record_outcome call

```json
{
  "call_id": "mcp_c3d4e5f6789012345678901234cdef01",
  "useful": false,
  "changed_decision": false,
  "impact": "low",
  "category": "dependency",
  "notes": "Synthesize answered a broader question than I asked — duplicated what I already knew about date-library tradeoffs without addressing whether Date.parse() handles ISO-8601 strings reliably."
}
```

## Why these values

- `useful=false` — duplicated what you already knew; didn't engage with the actual narrow question.
- `changed_decision=false` — original plan stood; the response was noise.
- `impact=low` — `Date.parse()` is trivially reversible if it doesn't work; isolated to one call site.
- `category=dependency` — the question was about library choice (use a date library or not).
- `notes` — REQUIRED because `useful=false`. Explains the specific failure mode (answered a different question) without disclosing code details.

This is the "wasted call" quadrant. Surfacing it on the dashboard is important — it tells the user when TruVerifAI isn't worth the call. Notes are required precisely because no-op cases are the most informative for honest signal.
