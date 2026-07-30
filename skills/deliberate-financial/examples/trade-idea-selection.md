# Example — ranking candidate trade ideas

**Situation:** three candidate ideas survived generation; pick which (if any) to pursue.

**Call:**
```json
{
  "tool": "deliberate_financial",
  "arguments": {
    "question": "Which of these three has the best risk-adjusted edge for a 3-week hold, or is none worth it?",
    "options_considered": "(A) Long XYZ post-breakout. (B) Pairs: long ABC / short DEF on spread divergence. (C) Sector-rotation tilt into industrials.",
    "relevant_data": "[ADV, spreads, recent vol, the spread series for B, sector flows for C]",
    "assumptions": "Elevated vol regime holds 3 weeks; no leverage; 2% NAV max single-name drawdown.",
    "constraints": "Long/short allowed for the pair; mandate caps single-name at 5% NAV."
  }
}
```

**What good output looks like:** the deliberation **ranks in prose** (D1 — no separate structured ranking field), weighing each option's **edge mechanism, net edge after costs, why-not-priced-in, crowding/decay, and downside** to the *same* bar, then gives a `recommendation` + server-derived `action`:

> Conclusion: B (the pair) has the only mechanism-grounded, cost-aware edge — spread divergence with a clear mean-reversion catalyst and a stated invalidation (spread widening past X). A is a chart pattern with no edge after costs ("why not priced in?" unanswered). C is regime-dependent and crowded. **Recommend B; pass on A and C.**

`recommendation: qualified` → `action: review_assumptions`; an `edge_quality` finding on A ("pattern-only, no durable edge") and a `crowding_decay` finding on C are expected. Note the panel **earns the "pass" on A/C to the same bar** as the "pursue" on B — not a reflexive dismissal.

**What to do next:** `audit-financial` on B before committing. Then `record_outcome`. **Reasoning, not advice.**
