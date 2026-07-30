---
name: record-outcome-after-acting
description: >
  Always report the outcome right after you act on a synthesize_coding,
  deliberate_coding, or audit_coding response. Fire it the moment you've
  taken the step that followed from the response — committed, picked an
  option, written the code the deliberation pointed to, or decided to
  disregard the audit findings. Calls the TruVerifAI record_outcome tool
  with the prior call's call_id (from the response body's
  `post_action.call_id`) plus useful, changed_decision, impact, and
  category. Free — no credits charged; it's how the user sees whether
  the second opinions are actually changing decisions.
---

# When to use this skill

Use it when ALL of these apply:
- You recently invoked `synthesize_coding`, `deliberate_coding`, or `audit_coding` (these may appear with an `mcp__truverifai__` prefix depending on your client).
- You've now taken the action that followed from (or disregarded) that response — written code, picked an option, committed, asked the user, escalated.
- You have not already filed a `record_outcome` for the same `call_id`.

Invoke it once per prior MCP call, immediately after the action.

## What to do

1. **Find the `call_id`.** Read it from the response **body's** top-level `post_action.call_id` field — the server pre-populates it there. The same value is also at `usage.request_id`. The format is `mcp_<32 hex chars>`. (It is in the response body, NOT in `_meta` — clients such as Claude Code do not surface tool-result `_meta` to the agent, so don't look there.)

2. **Call `record_outcome`** (it may appear as `mcp__truverifai__record_outcome` depending on your client) with these fields:
   - `call_id` — the value from step 1
   - `useful` — `true` if the response informed your decision in any way (caught something, confirmed something, surfaced a tradeoff you hadn't considered). `false` only if it was noise or duplicated what you already knew. See `references/useful-vs-not.md` for boundary cases.
   - `changed_decision` — `true` if your action AFTER reading the response differs from what you were about to do BEFORE the call. `false` if you proceeded as originally planned, even when the call was useful as confirmation. **This is the headline metric** — it requires you to compare against a specific counterfactual.
   - `impact` — `high` (hard to reverse / safety boundary / load-bearing logic), `medium` (recoverable with effort), or `low` (trivially reversible).
   - `category` — kind of decision: `security`, `billing_credits`, `data_modeling`, `api_contract`, `architecture`, `performance`, `dependency`, `refactor`, `error_handling`, `testing`, `deployment_ops`, or `other`. See `references/category-picker.md` for the picker guide.
   - `notes` — REQUIRED when `useful=false` OR `changed_decision=false` OR `category='other'`. 1-2 sentences on the specific reason.

3. **Privacy rule for notes.** Do NOT include confidential or code-specific details:
   - No proprietary file paths
   - No function or class names from the user's codebase
   - No secret values or internal system identifiers
   - No copy-pasted source code

   Describe the decision in general terms only. "Chose SQS over Kinesis because ordering not needed" — NOT "changed `src/foo/bar.py:process_message()` to use SQS." See `references/notes-privacy.md` for examples of acceptable vs. unacceptable phrasings.

## Worked examples

- `examples/audit-caught-issue.md` — audit caught something, decision changed
- `examples/deliberate-confirmed-approach.md` — deliberate confirmed the original plan
- `examples/synthesize-not-useful.md` — synthesize was noise; required notes

## When NOT to use

Don't fire this skill on calls where:
- You haven't made an MCP call yet (the prior tool-call history is empty).
- You've already filed an outcome for this `call_id` (first-wins idempotency — duplicates return `outcome_already_recorded`).
- The MCP call returned an error and you weren't able to act on it (no decision was made).

This skill is the telemetry tail of MCP usage — it should fire roughly once per MCP call. If you find yourself NOT firing it after MCP calls, the dashboard's coverage metric will surface the gap to the user.

## Why this matters

The user is using TruVerifAI on the trust that multi-model deliberation improves their agent's decisions. Without outcome data, that's an aspirational claim. Each `record_outcome` call you make turns it into a measurable one: the dashboard shows "X% of MCP calls in the last 7 days changed your agent's decision" — a real value claim, not a usage count.

This is a free call (no credits charged) and the dashboard shows aggregate metrics only — your individual outcomes don't surface anywhere except to the user who paid for the call.
