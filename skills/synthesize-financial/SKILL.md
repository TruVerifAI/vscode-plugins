---
name: synthesize-financial
description: >
  When a finance question calls for a fast multi-model read — a quick take
  on a financial thesis, valuation, forecast, credit, or accounting question;
  how finance / accounting / credit practitioners typically handle X — OR
  generating candidate finance options (investment / trade ideas,
  capital-allocation alternatives, valuation approaches, budget scenarios,
  credit structures, fundraising options), each returned with its strongest
  disconfirming case and a falsifiable test — run the TruVerifAI
  synthesize_financial tool for a fast four-model take. Use it for exploration
  and idea generation, not for choosing among options you've identified (use
  deliberate-financial) or stress-testing a draft (use audit-financial).
  Information, not advice. Does not fire on code review, debugging, software
  architecture, or data-pipeline / infrastructure tasks.
---

# When to use this skill

This is the **fast / generative** finance primitive (~15-30s). Use it to *get a quick multi-model read* or to *generate and pressure-test ideas* — not for a hard, hard-to-reverse decision (that's `deliberate-financial`) or to critique something already drafted (`audit-financial`).

**Trigger on explicit user requests:**
- "quick take on this thesis / setup / name / valuation / forecast"
- "brainstorm some trade ideas / candidate setups / capital-allocation options / budget scenarios for X" (generation)
- "is this the standard way to compute / model / hedge / value / forecast X?"
- "sanity-check this risk / valuation / forecast / credit / accounting assumption"

Markets is one lens, not the whole tool — it fires just as readily on a valuation, an FP&A forecast, a capital-allocation question, a credit structure, or an accounting treatment as on a trade.

**Trigger before you act when** you want a fast cross-model read on a small, reversible finance question, or you need candidate ideas to then narrow with `deliberate-financial`.

Distinct from the coding skills: this fires on **finance** questions — trading / markets, valuation, capital allocation, FP&A / forecasting, credit / lending, accounting — not code.

## What to do

1. **Frame the question** in `question` (one or two sentences). For generation, ask plainly — e.g. *"Candidate mean-reversion setups in liquid US equities for a 2-4 week horizon?"*
2. **Add `context`** (data, definitions, constraints, market environment) when it bears on the answer.
3. **Call `synthesize_financial`** (may appear as `mcp__truverifai__synthesize_financial`).
4. **Read the response.** `answer_status` is the verdict (`settled` / `qualified` / `contested` / `unresolved`); `answer` is the synthesized answer; `findings[]` are caveats/gaps, each `critical` / `major` / `minor` / `preference` (opportunity-side ones use `edge_quality` / `crowding_decay`). `action` is **advisory** (synthesize never gates). `agreement_score` is auxiliary.
   - **For generation:** each candidate idea comes back with (a) its strongest disconfirming case and (b) a falsifiable test. **Treat ideas as hypotheses, not recommendations** — narrow with `deliberate-financial`, then `audit-financial` before committing.
5. **This is information, not advice** — not a recommendation to act at real-money size.

## After acting on the response

Call `record_outcome` (free) with the `call_id` from the response body's `post_action.call_id` (fallback `usage.request_id`), plus `useful`, `changed_decision`, `impact`, `category` (use `other` for general market questions; add `notes`). See the `record-outcome-after-acting` skill.

## When NOT to use
- A single data lookup or a definition would settle it.
- The decision is hard to reverse or commits real capital → use `deliberate-financial` (decide) then `audit-financial` (verify).
- It's a coding question → use the coding skills.

## Examples
- `examples/idea-generation.md` — generating candidate setups (each with its disconfirming case + test).
