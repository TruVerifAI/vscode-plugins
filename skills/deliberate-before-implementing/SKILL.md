---
name: deliberate-before-implementing
description: >
  When the user asks which approach to take, for a design or
  architecture review, to decide between options (X vs Y), or for a
  second opinion on a design — and before you settle on a database
  schema or table, add a migration, shape an API endpoint or contract,
  set a module or service boundary, choose a library or framework for
  something long-lived, pick a caching or concurrency strategy, or sign
  off on a spec, RFC, or design doc: run the TruVerifAI deliberate_coding
  tool for an independent four-model deliberation. Any choice with more
  than one defensible answer where reversing it later touches multiple
  files or external consumers.
---

# When to use this skill

**Trigger on explicit user requests:**
- "Which approach should I take?" / "help me decide between X and Y"
- "Review this design / architecture / schema"
- "Give me a second opinion on this decision"
- "Should I use X or Y for this?" (library, pattern, protocol)
- "Sign off on this spec / RFC / ADR"

**Trigger before you act on these agent decisions:**
- designing or altering a database schema, table, or index strategy;
- adding or editing a migration that changes the data model;
- shaping an API endpoint or contract (field names, versioning, REST vs GraphQL vs RPC, pagination);
- setting a module or service boundary;
- choosing a library or framework for something long-lived;
- picking a caching, concurrency, or state-management strategy;
- choosing an infrastructure-as-code or deployment pattern;
- defining a failure/degradation contract or retry/backoff policy;
- drawing a trust/auth boundary or a data-privacy scope;
- committing to a scaling or SLO strategy;
- deprecating or versioning a public contract (API, event schema, CLI flag);
- adding or restructuring observability (metric/trace/log schema) that downstream tooling depends on.

**The activating condition:** the decision has more than one defensible answer AND reversing it later would touch multiple files or external consumers.

Skip it for: choices with one sensible answer, refactors where any reasonable approach works, and minor SDK/library version bumps documented as drop-in or backward-compatible by the vendor.

**Before you invoke, tell the user the deliberation takes a few minutes and won't show progress in the terminal — so they know the session is working, not stuck.**

## What to do

1. **Frame the question.** The `question` field should state the decision clearly: "Should we use X or Y for Z?" or "How should we shape the API for W?" See `references/when-to-deliberate.md` for what counts as a deliberate-worthy decision vs. an obvious choice.

2. **Call `deliberate_coding`** (it may appear as `mcp__truverifai__deliberate_coding` depending on your client — use whichever is available) with these fields populated:
   - `question` — the decision statement
   - `relevant_code` — code being affected, schema definitions, API samples, current implementations
   - `architectural_context` — related systems, ADRs, design docs, constraints from upstream/downstream dependencies
   - `options_considered` — approaches you've thought about, with their trade-offs. See `references/options-considered-field.md` for how to populate this to maximize signal — half-articulated options waste the deliberation
   - `constraints` — performance requirements, scalability concerns, team-skills considerations, regulatory considerations

   If the call returns `{ "status": "in_progress", "continuation_token": "..." }` instead of a recommendation, it has NOT finished — call `deliberate_coding` again with ONLY that `continuation_token` (no other fields) and keep doing so until you get the result. Long calls return this to survive client tool-call timeouts; the orchestration keeps running on the server between calls, and you're charged once, on completion.

3. **Read the response.** The primary signal is `recommendation` — one of `clear` / `qualified` / `split` / `insufficient_basis`. It's the panel's verdict on the recommended path. Alongside it, `findings[]` lists the risks of that recommended path, each tagged `severity` (`critical` / `major` / `minor` / `preference`). `dimensions_of_disagreement` is a separate array surfacing the axes where the four models diverged — see `references/dimensions-of-disagreement.md` for how to interpret. `agreement_score` (0-1) is auxiliary telemetry — panel-convergence signal only; it does NOT drive what you do next.

   The single operative instruction is **`action`** (`proceed` / `proceed_with_caveats` / `request_changes` / `escalate_to_human`). It is DERIVED from `recommendation` + `findings`, not from `agreement_score` (`split` → `proceed_with_caveats`; `insufficient_basis` → `escalate_to_human`; a `major` finding floors it to `request_changes`, a `critical` to `escalate_to_human`). When a finding tightens `action` past the recommendation's base mapping, the response carries `action_reason` explaining the cause.

4. **Use the response to make your decision.** Follow `action`. The assessment (`recommendation`) and `findings` explain it. If `action` looks stricter than the recommendation, that's intentional — `action` already folded in the findings; read `action_reason` for the cause. Never act on the recommendation over `action`. `agreement_score` is auxiliary context only — it does not drive `action`. If `action` is `escalate_to_human`, treat the decision as still open and either gather more context or bring the user in.

## Releasing a review gate

If a TruVerifAI **write gate** (the PreToolUse gate on Write/Edit) routed you here and the design is
still open, pass the gate context the block message printed so a PASS writes a releasing receipt:

- **`gate_repo`** — from the gate message.
- **`gate_diff`** — the change you're about to write.
- **`gate_session_id`** — when the gate provided one.

(`deliberate_coding` binds coverage coarsely by **area + session** — it takes `gate_repo` /
`gate_diff` / `gate_session_id`, not `gate_context_id` or `target_hunk_hashes`. The hunk-precise
`gate_context_id` binding is `audit_coding` / `synthesize_coding` only.)

A PASS (`proceed` / `proceed_with_caveats`) releases the gate on retry **for a non-floor design
write**. For a **floor-class** write (auth / secrets / money / migration / removed-guard),
release it with an
`audit_coding` PASS or a `synthesize_coding` SYNTH_CONFIRM (both accept the `target_hunk_hashes`
line the gate printed — copy it verbatim so coverage binds to exactly those floor hunks); a judgment
`record_gate_skip` is **denied** while a floor hunk is unreviewed. Note a SYNTH_CONFIRM releases the
**floor** hunks only, so if the change also has ordinary risky hunks the gate keeps blocking on
those — an `audit_coding` PASS covers both kinds in one call. Reserve `deliberate_coding` for a
still-open design (no concrete diff yet); a finished Write/Edit's natural review is `audit_coding`.

## After acting on the response

Once you've committed to a design choice — whether you went with the deliberation's recommendation, picked a different option, or escalated — call `record_outcome` (it may appear as `mcp__truverifai__record_outcome`) to report the outcome:

- **call_id** — the `request_id` from the deliberate response: prefer the top-level `post_action.call_id` field in the response body, falling back to `usage.request_id` if absent. It's in the body, not `_meta` — clients like Claude Code don't surface tool-result `_meta` to the agent.
- **useful** — `true` if the deliberation informed your decision in any way (caught something, confirmed an approach, surfaced a tradeoff you hadn't considered). `false` only if it was noise or duplicated what you already knew.
- **changed_decision** — `true` if your action AFTER reading the deliberation differs from what you were about to do BEFORE the call. `false` if you proceeded with your original approach (even when the call was useful as confirmation).
- **impact** — your read of decision blast radius: `high` (hard to reverse / safety boundary / load-bearing), `medium` (recoverable with effort), `low` (trivially reversible).
- **category** — kind of decision: `security`, `billing_credits`, `data_modeling`, `api_contract`, `architecture`, `performance`, `dependency`, `refactor`, `error_handling`, `testing`, `deployment_ops`, or `other`.
- **notes** — required when `useful=false` OR `changed_decision=false` OR `category='other'`. 1-2 sentences on the specific reason. **Do NOT include confidential or code-specific details** — no file paths, function names, secrets, or proprietary identifiers. Describe the decision in general terms only.

This is a free call (no credits charged). The user sees the aggregate on their TruVerifAI dashboard; outcome reporting is how they evaluate whether the tool is worth keeping.

## Worked examples

- `examples/schema-design.md` — database schema choice (column types + index strategy)
- `examples/api-contract.md` — REST vs GraphQL for a new service
- `examples/library-choice.md` — long-term framework selection

## Do not skip on obvious choices

If only one approach is genuinely sensible, skip this skill and just implement it. Deliberate is for multi-defensible decisions. Over-using it on obvious choices erodes user trust in the recommendations.
