# Example: audit caught an issue, decision changed

## Setup

You just ran `mcp__truverifai__audit_coding` on a Stripe webhook handler change. The audit returned `verdict=request_changes` (with `action=request_changes` derived from it) and a critical-severity finding: the signature-verification step was using a constant-time-compare wrapper but the input was being decoded as a string instead of bytes, so the comparison was vulnerable to a timing attack.

You revised the change to fix the encoding issue.

## record_outcome call

```json
{
  "call_id": "mcp_a1b2c3d4e5f6789012345678901234ab",
  "useful": true,
  "changed_decision": true,
  "impact": "high",
  "category": "security",
  "notes": "Audit caught a webhook signature-verification timing-attack vulnerability that required a fix before commit."
}
```

## Why these values

- `useful=true` — the audit caught a real issue.
- `changed_decision=true` — you would have committed the original code without the audit; the revision differs.
- `impact=high` — security boundary; payment flow; would have shipped to prod.
- `category=security` — webhook signature verification is auth-adjacent.
- `notes` — describes the SHAPE of the issue (timing attack on signature verification) without naming files or functions.

This is the four-quadrant "caught something I would have missed" case — the headline value claim TruVerifAI makes.
