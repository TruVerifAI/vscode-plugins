# Example: deliberate confirmed the original plan

## Setup

You were choosing between two designs for a new caching layer — application-side LRU vs. a shared Redis instance. You'd already decided on application-side LRU based on the small dataset size and low write rate. Before committing, you ran `mcp__truverifai__deliberate_coding` to check.

The deliberation returned `recommendation=clear, action=proceed` (agreement_score 0.81, auxiliary), agreeing that application-side LRU is the right call for this scale.

## record_outcome call

```json
{
  "call_id": "mcp_b2c3d4e5f6789012345678901234bcde",
  "useful": true,
  "changed_decision": false,
  "impact": "medium",
  "category": "performance",
  "notes": "Deliberation confirmed the original approach (application-side LRU for this data scale); proceeded as planned with more confidence."
}
```

## Why these values

- `useful=true` — the deliberation validated the approach with concrete reasoning about the data-size threshold; you proceeded with more confidence.
- `changed_decision=false` — you went with the original plan. Importantly, you did NOT mark `useful=false` just because the decision didn't change — confirmation has value.
- `impact=medium` — caching strategy is recoverable but the migration cost to a shared cache later would be non-trivial.
- `category=performance` — caching strategy.
- `notes` — **REQUIRED here** because `changed_decision=false` (one of the three triggers: `useful=false` OR `changed_decision=false` OR `category='other'`). The "confirmed, didn't change" quadrant always needs a one-line note — these no-op cases are the most informative signal for the user. Keep it general (no file/function names).

This is the "useful but not changed" quadrant — synthesize-style confirmation. Common for deliberate calls where you'd already done careful thinking. Because the decision didn't change, the note is what tells the user the call still mattered.
