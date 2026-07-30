---
name: deliberate-financial
description: >
  When you are still deciding among two or more defensible finance options —
  which trade or investment to pursue, position sizing, a valuation, a
  capital-allocation choice (M&A / capex / buyback), a financial forecast or
  budget assumption, a credit decision, an accounting treatment, a risk-model
  or methodology choice — run the TruVerifAI deliberate_financial tool for
  independent four-model deliberation. Use it when you are still choosing,
  not when you have already drafted a plan (use audit-financial). It weighs
  the affirmative case and the downside to the same bar (for markets
  decisions, whether there's a genuine edge after costs) and surfaces where
  the models genuinely disagree. Information / reasoning, not advice. Does
  not fire on code review, debugging, software architecture, or data-pipeline
  / infrastructure tasks. For a fast take or to generate options use
  synthesize-financial.
---

# When to use this skill

This is the **decision / selection** finance primitive (~2-5 min): four models reason, conflicts are surfaced and defended/revised, then synthesized. Use it to **choose or compare** — not for a fast take (`synthesize-financial`) or to critique a finished draft (`audit-financial`).

**Trigger on explicit user requests:**
- "which of these trades / strategies / investments should I pursue?" (ranking — the panel ranks in prose)
- "long or short here / how should I size this?"
- "what's this worth — which valuation approach / what discount rate / multiple?" (a valuation call)
- "build vs buy / buyback vs reinvest / which capex / fund this acquisition?" (capital allocation)
- "is this revenue forecast / budget assumption defensible?" (FP&A / forecast)
- "approve or decline this loan / credit? what structure / covenants?"
- "which accounting treatment / how should this be recognized or disclosed?"
- "parametric vs historical-sim VaR?" / a risk-model or methodology choice

**Trigger before you act when** you're settling a finance decision with more than one defensible answer where being wrong is costly.

Distinct from `deliberate-before-implementing` (coding): this fires on **finance** decisions — trading / markets, valuation, capital allocation, FP&A / forecasting, credit / lending, accounting, risk.

## What to do

1. **State the decision** in `question`. For a ranking, list the candidates in `options_considered`.
2. **Provide** `relevant_data` (prices, factor data, model output), `assumptions` (market environment, vol, liquidity, horizon, leverage), and `constraints` (risk limits, mandate, capital).
3. **Call `deliberate_financial`** (may appear as `mcp__truverifai__deliberate_financial`). If it returns `{ "status": "in_progress", "continuation_token": "..." }` instead of a recommendation, it has NOT finished — call `deliberate_financial` again with ONLY that `continuation_token` (no other fields), repeating until you get the result. Long calls return this to survive client tool-call timeouts; the work continues on the server and you're charged once, on completion.
4. **Read the response.**
   - `recommendation` is the primary signal: `clear` / `qualified` / `split` / `insufficient_basis`. `conclusion` is the reasoned call.
   - `findings[]` are risks/weaknesses of the recommended path (risk- *and* opportunity-side — e.g. `edge_quality` / `crowding_decay`); `dimensions_of_disagreement` shows residual splits.
   - `action` is **server-derived** (`proceed` / `review_assumptions` / `gather_more_data` / `escalate_to_human`) — act on `action`, not `agreement_score` (auxiliary telemetry).
   - The deliberation defaults to the most **calibrated** conclusion and holds *both* an "edge" and a "no-edge" call to the same bar — so a `gather_more_data` / "no durable edge" result is a real, useful answer, not a failure.
5. **This is reasoning, not advice** — not a recommendation to act at real-money size.

## After acting on the response

Call `record_outcome` (free) with `post_action.call_id` + `useful` / `changed_decision` / `impact` / `category`. See the `record-outcome-after-acting` skill.

## When NOT to use
- A fast, reversible read → `synthesize-financial`.
- You've already drafted the trade/strategy and want it stress-tested → `audit-financial`.
- It's a coding decision → `deliberate-before-implementing`.

## Examples
- `examples/trade-idea-selection.md` — ranking candidate trade ideas.
