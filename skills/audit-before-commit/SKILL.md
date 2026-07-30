---
name: audit-before-commit
description: >
  Invoke immediately when the user asks to review, audit, or check a
  diff or PR — including soft requests ("mind taking a look?", "before
  I approve this"). Also invoke before you commit, merge, or approve any
  change touching auth/authz, billing or money flow, secrets or
  credentials, input validation, data migrations, infrastructure-as-code
  or deploy config, load-bearing logic, a removed or weakened guard, a
  breaking API or schema change, or a dependency upgrade on a security
  or API boundary. Routes the change to the TruVerifAI audit_coding tool
  for an independent four-model blind-spot review.
---

# When to use this skill

**Invoke immediately when the user:**
- asks you to review, audit, or stress-test a change, or to do a code review;
- says "code review", "look at this diff", "before I merge/approve" — or anything soft like "mind taking a look?" or "does this look right?".

**Invoke before you commit, merge, or approve any change that:**
- touches authentication/authorization, cryptography, input parsing/validation, payment or money flow, secrets or credential handling, PII, or persistence-layer code;
- removes or weakens an existing guard, gate, or validation check — even a one-line change;
- introduces a breaking API or schema change;
- upgrades a dependency on a security or API boundary;
- replaces or refactors load-bearing logic many callers depend on, or is hard to reverse (rollback touches multiple files or needs a migration);
- touches infrastructure-as-code or deployment config.

**Also invoke when reviewing a teammate's diff** in any of those areas — even when phrased softly ("mind taking a look?", "before I approve").

Skip it for: formatting or comment-only edits, doc/README updates, single-line obvious bug fixes, scalar config changes (timeouts, retries, thresholds) with clearly low-risk impact — but do NOT skip if the config touches security, money flow, or production safety — routine index/column additions with bounded schema impact, and string-literal/typo fixes.

**Before you invoke, tell the user the audit takes a few minutes and won't show progress in the terminal — so they know the session is working, not stuck.**

## What to do

1. **Frame the change for the audit.** The `proposed_action` field should be 2-4 sentences summarizing WHAT changes and WHY — NOT the raw diff. The raw diff goes into `relevant_code`. See `references/structured-inputs.md` for the populate rules per field.

2. **Call `audit_coding`** (it may appear as `mcp__truverifai__audit_coding` depending on your client — use whichever is available) with these fields populated:
   - `proposed_action` — your 2-4 sentence framing
   - `relevant_code` — the diff or the before/after for the changed code
   - `tests` — existing tests covering the affected code, plus any new tests added alongside
   - `architectural_context` — related systems, design decisions, ADRs. If unsure what's relevant, name the high-level system the change belongs to ("auth layer", "payment flow", "persistence layer")
   - `constraints` — performance bounds, security policies, deployment windows, regulatory considerations

   If the call returns `{ "status": "in_progress", "continuation_token": "..." }` instead of a verdict, it has NOT finished — call `audit_coding` again with ONLY that `continuation_token` (no other fields) and keep doing so until you get the verdict. Long calls return this to survive client tool-call timeouts; the orchestration keeps running on the server between calls, and you're charged once, on completion.

3. **Read the response.** The primary signal is `verdict` — one of `approve / approve_with_caveats / request_changes / reject` — the audit's per-change assessment. Every response also carries `findings[]`, each tagged with a `severity` (critical / major / minor / preference); see `references/severity-tags.md` for interpretation. The `action` field carries one of four enum values and is the single instruction you obey — it's DERIVED from the verdict + findings, not from `agreement_score`. When a finding tightens `action` beyond the verdict's base mapping, `action_reason` explains why. See `references/action-classes.md` for what to do with each.

   > Follow `action`. The assessment (`verdict`) and `findings` explain it. If `action` looks stricter than the verdict, that's intentional — `action` already folded in the findings; read `action_reason` for the cause. Never act on the verdict over `action`. `agreement_score` is auxiliary context only — it does not drive `action`.

4. **Revise the change** in response to the findings. If `action` is `escalate_to_human`, do NOT proceed without the user's input.

## After acting on the response

Once you've decided what to do with the audit's findings — whether you accepted them, revised your plan, or kept your original approach — call `record_outcome` (it may appear as `mcp__truverifai__record_outcome`) to report the outcome:

- **call_id** — the `request_id` from the audit response: prefer the top-level `post_action.call_id` field in the response body, falling back to `usage.request_id` if absent. It's in the body, not `_meta` — clients like Claude Code don't surface tool-result `_meta` to the agent.
- **useful** — `true` if the audit caught something, confirmed something, or surfaced a tradeoff you hadn't considered. `false` only if it was noise or duplicated what you already knew.
- **changed_decision** — `true` if your action AFTER reading the audit differs from what you were about to do BEFORE the call. `false` if you proceeded as originally planned (even when the audit was useful as confirmation).
- **impact** — your read of decision blast radius: `high` (hard to reverse / safety boundary / load-bearing), `medium` (recoverable with effort), `low` (trivially reversible).
- **category** — kind of decision being audited: `security`, `billing_credits`, `data_modeling`, `api_contract`, `architecture`, `performance`, `dependency`, `refactor`, `error_handling`, `testing`, `deployment_ops`, or `other`.
- **notes** — required when `useful=false` OR `changed_decision=false` OR `category='other'`. 1-2 sentences on the specific reason. **Do NOT include confidential or code-specific details** — no file paths, function names, secrets, or proprietary identifiers. Describe the decision in general terms only.

This is a free call (no credits charged). The user sees the aggregate on their TruVerifAI dashboard; outcome reporting is how they evaluate whether the tool is worth keeping.

## Releasing a review gate

If a TruVerifAI **commit gate** (git commit) or **write gate** (Write/Edit) routed you here, pass
the gate context the block message printed so a PASS writes a releasing receipt bound to the flagged
hunks. `audit_coding` is the natural review for a finished Write/Edit, and a PASS releases the write
gate — including on a **floor** change:

- **`gate_repo`** — from the gate message.
- **`gate_diff`** — the staged diff being committed (or the content being written).
- **`gate_context_id`** — the `gc_…` the gate printed. **Pass it** — coverage then binds to the
  gate's OWN recorded hunks, so a cosmetically drifted `gate_diff` (a smart-quote, an em-dash) still
  releases the change instead of silently missing coverage.
- **`target_hunk_hashes`** — the **write gate** prints a `target_hunk_hashes = [...]` line on every
  block. **Copy it whole and verbatim.** Coverage binds *deterministically* to exactly the hashes you
  pass, so the change releases even when the write gate's diff shape differs from your `gate_diff` —
  and a **partial** list narrows what this PASS covers, leaving the rest blocked. The line lists every
  risky hunk of the change, so copying it whole is what you want. (The commit gate prints no such
  line; there, the `gate_context_id` binds coverage on its own.)

(`audit_coding` takes `gate_repo` / `gate_diff` / `gate_context_id` / `target_hunk_hashes` — it does
**not** take a `gate_session_id`.)

A **PASS-level action** (`proceed` / `proceed_with_caveats` — i.e. a verdict of `approve` /
`approve_with_caveats` with no finding that tightened the action) releases the gate on retry. (A
critical finding can floor an `approve` verdict to `escalate_to_human`, which does not release.)

An `audit_coding` PASS covers **floor and non-floor hunks alike**, which is why it's the one call
that clears a mixed change in a single step. That matters, because the two kinds of hunk release
**separately**: while a **floor** hunk (auth / secrets / money / migrations / removed-guard) is
unreviewed, a judgment `record_gate_skip` is **denied** (sole exception:
`recommendations_applied` with your recent review on record — applying a review's findings
releases the re-fired floor too), and the floor tools (`confirm_floor`,
`synthesize_coding`, `accept_risk_no_review`) release floor hunks **only** — so on a mixed change
one of those can succeed while the gate still blocks on the ordinary hunks it never touched. Read
the gate's `Still uncovered: N floor, M non-floor` line and use that bucket's tool.

For a genuine floor change, this `audit_coding` PASS is the recommended release. If you instead
believe the gate mis-fired, a free `confirm_floor` (or a `synthesize_coding` SYNTH_CONFIRM, ~15–30s)
clears the floor hunks — but only if the model agrees the change isn't actually risky. Under a
sustained review-tool outage the gate asks a human. (After you run ONE review,
`record_gate_skip(recommendations_applied)` — findings applied — or `review_deferred_to_commit` — a
batch — also release a floor **write**, but the floor is re-audited at commit.)

## Worked examples

- `examples/auth-change.md` — authentication-layer change (token validation logic)
- `examples/schema-migration.md` — database migration adding a column
- `examples/pr-review.md` — first review of someone else's non-trivial PR

## Do not skip

The "small diff" / "the contract is specified" / "I'm confident" framings are the failure mode this skill exists to prevent. Empirically, multi-model audits catch ship-blocking issues precisely on the diffs that feel obvious — on the project that built this MCP server, retroactive audit_coding calls caught multiple regressions that had already shipped in commits.

Cost of being wrong: a customer-facing regression you have to ship a hotfix for. Run the audit even when you think it won't add value.
