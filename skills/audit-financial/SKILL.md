---
name: audit-financial
description: >
  Before acting on a drafted finance decision, or to review a finance
  document / model / output — a trade or strategy and its backtest
  (survivorship / lookahead / leakage), a valuation or financial model (DCF /
  LBO / credit), a forecast or budget, a financial or portfolio risk report, an
  accounting / disclosure memo, a capital-allocation proposal, a lending /
  credit approval — run the TruVerifAI audit_financial tool for an independent
  four-model blind-spot critique (tail risk, liquidity, model risk, leverage,
  data quality, weak edge, crowding, valuation/forecast assumptions, accounting
  judgment). Also fires when the user asks to review / check / audit /
  stress-test / "look over" a finance artifact before acting. Information /
  critique, not advice. Does not fire on code review, debugging, software
  architecture, or data-pipeline / infrastructure tasks. To decide between
  options use deliberate-financial; for a fast take use synthesize-financial.
---

# When to use this skill

This is the **verification** finance primitive (~2-5 min): stress-test a decision *you've already drafted* before it's acted on at real-money size.

**Trigger on explicit user requests** (including soft ones — "mind looking this over before I place it?"):
- "audit / review / check this trade thesis / strategy / backtest"
- "is this backtest trustworthy?" (survivorship / lookahead / leakage)
- "stress-test this DCF / LBO / valuation model before we send it"
- "does this revenue forecast / budget hold up?" (FP&A / forecast)
- "review this capital-allocation memo (acquisition / capex / buyback)"
- "sanity-check this risk model / hedging plan before we ship it"
- "should we approve this loan?" (as a critique of the drafted approval)
- "is this accounting treatment / disclosure defensible?"

**Trigger before you act** on a drafted, consequential finance decision — placing/sizing a trade, deploying capital, approving credit, signing off a valuation or forecast, taking a risk model live, or finalizing an accounting treatment.

Distinct from `audit-before-commit` (coding): this fires on **finance** artifacts — trades / strategies / backtests, valuations & models, forecasts & budgets, capital-allocation memos, credit approvals, risk reports, accounting & disclosure — not git commits.

## What to do

1. **Put the drafted decision** in `proposed_action` — the trade thesis, sizing, strategy, backtest interpretation, or approve/decline call. Don't pass a one-liner; the audit needs enough to ground the critique.
2. **Provide** `relevant_data`, `assumptions`, `tests_backtests` (backtests / scenarios / stress tests), and `constraints` (risk limits, mandate, capital).
3. **Call `audit_financial`** (may appear as `mcp__truverifai__audit_financial`). If it returns `{ "status": "in_progress", "continuation_token": "..." }` instead of a verdict, it has NOT finished — call `audit_financial` again with ONLY that `continuation_token` (no other fields), repeating until you get the verdict. Long calls return this to survive client tool-call timeouts; the work continues on the server and you're charged once, on completion.
4. **Read the response.**
   - `verdict` is the primary signal: `sound` / `sound_with_caveats` / `reconsider` / `reject`. `critique` is the prose.
   - `findings[]` are severity-tagged defects (`critical` / `major` / `minor` / `preference`), categorized (`tail_risk`, `liquidity`, `model_risk`, `leverage`, `data_quality`, `assumptions`, `edge_quality`, `crowding_decay`, `accounting_disclosure`).
   - `action` is **server-derived** and floored by finding severity (a `critical` finding → `escalate_to_human`) — act on `action`, not `agreement_score` (auxiliary telemetry; high agreement on a `reject` is expected).
5. **This is a critique, not advice** — and a `reconsider` means "fix the gaps before committing," not "never."

## After acting on the response

Call `record_outcome` (free) with `post_action.call_id` + `useful` / `changed_decision` / `impact` / `category`. See the `record-outcome-after-acting` skill.

## When NOT to use
- You haven't drafted anything yet / want to generate or choose → `synthesize-financial` or `deliberate-financial`.
- It's a code change → `audit-before-commit`.

## Examples
- `examples/backtest-audit.md` — auditing a strategy backtest for survivorship / lookahead / leakage.
