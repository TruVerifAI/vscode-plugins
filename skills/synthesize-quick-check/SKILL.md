---
name: synthesize-quick-check
description: >
  When the user asks for a quick second opinion, a sanity check, what
  the standard or idiomatic way to do something is, whether there's an
  established pattern, or which of a few options is conventional — and
  when you're about to pick an idiomatic pattern or a bounded, reversible
  library choice and want a fast cross-model check first: run the
  TruVerifAI synthesize_coding tool for a fast four-model second opinion.
  For hard-to-reverse, multi-file decisions, use the deliberate skill
  instead.
---

# When to use this skill

**Trigger on explicit user requests:**
- "quick second opinion" / "sanity check this"
- "what's the standard / idiomatic way to do X?"
- "is there an established pattern for this?"
- "is X or Y the conventional choice here?" (bounded, reversible)

**Trigger before you act when** you're about to pick an idiomatic pattern, or make a bounded, reversible library choice, and want a fast cross-model check first.

Lower stakes than the deliberate or audit skills — the answer is for a decision you can adjust later. Skip when you're already confident, when a single doc lookup would settle it, or when the prompt already gives bounded implementation details (just write the code). For long-term, multi-file, or performance-critical commitments use `deliberate-before-implementing` instead.

## What to do

1. **Frame the question concisely.** The `question` field should be one or two sentences — "What's the idiomatic way to X in Y?" or "Is there an established pattern for Z?"

2. **Optionally provide context** via the `context` field if there's stack-specific or constraint-specific framing the models need (e.g., "We're on Python 3.11 with FastAPI; the API needs to support sync and async callers").

3. **Call `synthesize_coding`** (it may appear as `mcp__truverifai__synthesize_coding` depending on your client — use whichever is available) with `question` and optional `context`. Faster than the other two primitives (~15-30 seconds).

4. **Read the response.** Read `answer_status` first — it's the synthesized verdict (`settled` / `qualified` / `contested` / `unresolved`). The `answer` field carries the synthesized answer itself; `findings[]` lists the caveats and gaps, each tagged `critical` / `major` / `minor` / `preference`. `action` is also emitted but is **advisory** — synthesize's own action never *blocks* (though a gate-routed synthesize on a **floor** change can still mint a SYNTH_CONFIRM that *releases* a gate — see below). `agreement_score` (0-1) is auxiliary convergence telemetry: it does NOT drive the verdict or action. The escalate signal is an `answer_status` of `contested` or `unresolved` (a low `agreement_score` — the profile treats **< 0.6** as real disagreement — is only a corroborating cue). When the status fires, consider escalating to `deliberate-before-implementing` for a more thorough look — see `references/quick-vs-deliberate.md` for the decision boundary.

5. **Apply the answer.** For low-stakes decisions this is usually the end of the loop. If the synthesize result raises questions you didn't expect, escalate to deliberate.

## After acting on the response

Once you've used (or rejected) the synthesized answer, call `record_outcome` (it may appear as `mcp__truverifai__record_outcome`) to report the outcome:

- **call_id** — the `request_id` from the synthesize response: prefer the top-level `post_action.call_id` field in the response body, falling back to `usage.request_id` if absent. It's in the body, not `_meta` — clients like Claude Code don't surface tool-result `_meta` to the agent.
- **useful** — `true` if the answer informed your decision in any way (confirmed your approach, surfaced an alternative, caught an edge case). `false` only if it was noise or duplicated what you already knew.
- **changed_decision** — `true` if your action AFTER reading the answer differs from what you were about to do BEFORE the call. `false` if you proceeded as originally planned. Synthesize is the fast-confirmation path, so `changed_decision=false` is common and informative — it means the existing approach was right.
- **impact** — your read of decision blast radius: `high` (hard to reverse / safety boundary / load-bearing — though if it were truly high-impact you should have used `deliberate` instead), `medium` (recoverable with effort), `low` (trivially reversible). Most synthesize calls are `low`.
- **category** — kind of decision: `security`, `billing_credits`, `data_modeling`, `api_contract`, `architecture`, `performance`, `dependency`, `refactor`, `error_handling`, `testing`, `deployment_ops`, or `other`.
- **notes** — required when `useful=false` OR `changed_decision=false` OR `category='other'`. 1-2 sentences on the specific reason. **Do NOT include confidential or code-specific details** — no file paths, function names, secrets, or proprietary identifiers. Describe the decision in general terms only.

This is a free call (no credits charged). The user sees the aggregate on their TruVerifAI dashboard; outcome reporting is how they evaluate whether the tool is worth keeping.

## Worked examples

- `examples/idiomatic-pattern.md` — language-idiom question
- `examples/library-sanity.md` — bounded library choice
- `examples/standard-pattern.md` — "is-there-a-known-pattern" question

## Releasing a review gate — including a floor-class false positive

When a TruVerifAI review gate blocks a change you judge a **genuine false positive**,
`synthesize_coding` is a cheap way to clear it — an *intended* path on a **floor
class** (auth / secrets / money / migration / removed-guard), where a one-line `record_gate_skip`
with a judgment code is denied while the floor hunk is unreviewed. (Even cheaper: **`confirm_floor`**
— a FREE one-budget-model check that mints the same SYNTH_CONFIRM if it agrees the floor change is
token-shape noise. Reach for it first on a suspected false floor; escalate to `synthesize_coding`'s
panel for a broader read.)

**It releases FLOOR hunks only.** On a change that also has ordinary risky hunks, a passing
`synthesize_coding` / `confirm_floor` clears the floor and the gate **keeps blocking** on the rest —
release those with a judgment `record_gate_skip` or an `audit_coding` PASS (which covers both kinds
in one call). The block message's `Still uncovered: N floor, M non-floor` line tells you which
bucket is left. And a verdict that is **not** a PASS mints nothing — the response says so
explicitly, so an affirmative-sounding answer is never mistaken for a release.

Pass the gate context the block message printed — the same fields either tool takes:

- **`gate_repo`** — copied from the gate message.
- **`gate_diff`** — the change being gated (the staged diff, or the content being written).
- **`gate_context_id`** — the `gc_…` the gate printed. **Pass it** — the SYNTH_CONFIRM then binds to
  the gate's OWN recorded floor hunks, so a cosmetically drifted `gate_diff` still releases.
- **`target_hunk_hashes`** — the **write gate** prints a `target_hunk_hashes = [...]` line on every
  block. **Copy it verbatim.** The SYNTH_CONFIRM binds to the **floor** hunks among them (a synthesize
  confirmation only ever covers floor), so it releases even when the write gate's diff shape differs
  from your `gate_diff`. (The commit gate prints no such line; there, the `gate_context_id` binds
  coverage on its own.)
- **`gate_session_id`** — when the gate provided one.

If the panel agrees the change is low-risk, the server mints a **SYNTH_CONFIRM** bound to the
change's **floor** hunks, and they release on retry — ~15–30s, no full `audit_coding`. This is the
intended cheap path to clear a **floor-class** gate (which `deliberate_coding` cannot release). If
any NON-floor hunks remain uncovered the gate keeps blocking on those; clear them with a judgment
`record_gate_skip` or an `audit_coding` PASS. If the panel surfaces real risk instead, run
`audit_coding`.

**Important — synthesize only releases a FLOOR gate.** SYNTH_CONFIRM is minted only on a floor
change; on a **non-floor** gate `synthesize_coding` writes **no receipt**, so it does not release
the gate — there, run `audit_coding` (a PASS releases) or `record_gate_skip`. (Ordinary synthesize
calls with no gate context also never write a receipt.)

## When NOT to use

If you can find the answer in 30 seconds with a search engine or a doc lookup, skip this skill. Synthesize is for moments where you want multiple models' takes on something genuinely ambiguous, not for moments where a single canonical answer exists in the docs.
