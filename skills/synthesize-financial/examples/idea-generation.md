# Example — generating candidate setups (idea + refutation)

**Situation:** the user wants a few candidate ideas to then narrow down — not a final pick.

**Call:**
```json
{
  "tool": "synthesize_financial",
  "arguments": {
    "question": "Candidate mean-reversion setups in liquid US large-caps for a 2-4 week horizon?",
    "context": "Long-only book, $50M AUM, names with >$50M ADV only, no leverage. Current regime: elevated vol, rates stable."
  }
}
```

**What good output looks like:** each generated idea is paired with its **strongest disconfirming case** and a **falsifiable test**, e.g.:

> **Idea:** post-earnings-drift fade on names that gapped >2σ on no fundamental change.
> **Disconfirming case:** the gap may encode real information (guidance, channel checks) not in the headline; "no fundamental change" is often an illusion of hindsight.
> **Falsifiable test:** over the next 10 sessions, does the name revert ≥50% of the gap *and* is there no analyst estimate revision >3%? If estimates revise, the thesis is wrong.
> **Edge vs cost:** at $50M ADV, 2-4 week holding, expected reversion must clear ~15-25 bps round-trip cost. **Why not already priced in:** if it were a clean anomaly it would be arbitraged — the residual edge, if any, is in the selection filter, not the pattern.

`answer_status` will typically be `qualified` or `contested` for generation (these are hypotheses, not settled answers).

**What to do next:** treat each idea as a hypothesis. Narrow with `deliberate-financial` (which has the strongest risk-adjusted edge?), then `audit-financial` the chosen one before any real-money action. **Information, not advice.**
