---
name: skip-gate-when-not-needed
description: >
  Release a TruVerifAI proactive review gate WITHOUT running (another) review.
  Use it when the commit gate (git commit) or the write gate (Write/Edit)
  blocked your action and
  EITHER (a) the review is genuinely unnecessary — a false positive, a
  trivial/generated/test-or-docs change, or a true time-critical hotfix — OR
  (b) you ran ONE review and want to proceed on its result: after applying its
  findings use recommendations_applied, or use review_deferred_to_commit to
  defer a batch of successive risky writes to the commit gate. Calls the
  TruVerifAI record_gate_skip tool with a structured reason_code (+ free-form
  text for judgment calls). Free — no credits. DEFAULT to actually running the
  suggested review; every release is logged.
---

# When to use this skill

Use it when ALL of these apply:
- A TruVerifAI review gate just **blocked** your action (a `git commit`, or a
  `Write`/`Edit`/`MultiEdit`) with a message naming `audit_coding`,
  `deliberate_coding`, or `synthesize_coding` and offering a skip.
- You have **genuinely** judged that running that review is unnecessary for this
  specific change (see "Legitimate reasons" below).
- You are NOT skipping merely to move faster, avoid latency, or because you're
  confident. Confidence is not a reason — that's exactly when a second opinion
  pays off.

If in doubt, **run the suggested review instead.** The gate fired because a local
classifier flagged the change; the classifier is tuned for high recall, so some
false positives are expected — but the cost of a wrong skip on a real risk is
much higher than ~15s–5min of review.

## What to do

1. **Read the gate message.** It prints, ready to copy verbatim:
   - `gate_repo` — always.
   - `gate_context_id = "gc_…"` — **the release key.** A server-issued id proving the gate
     fired; the server verifies it, consumes it single-use, and releases exactly the hunks
     **it** recorded, so you never supply hunks yourself. **Copy it verbatim.**
   - `gate_session_id` (when the write gate provides one) and a `gate_signal` line
     (`classifier_version` / `score` / `risk_categories`).

2. **Call `record_gate_skip`** (it may appear as `mcp__truverifai__record_gate_skip` depending on your client) with:
   - `gate_repo` — copied from the gate message.
   - `reason_code` — the closest fit from the enum (see `references/reason-codes.md`):
     `false_positive_not_risky`, `trivial_change`, `reviewed_outside_truverifai`,
     `generated_or_vendored_code`, `test_or_docs_only`, `time_critical_hotfix`,
     `disagree_with_classification`, `tool_unavailable`, `other` — plus the two
     **single-call** outcomes: `recommendations_applied` (you ran ONE review and applied
     its findings) and `review_deferred_to_commit` (defer a batch to the commit gate) — and
     `accept_risk_no_review`, the last-resort floor override (ships the change un-reviewed;
     see "Floor classes" below). See "The single-call model" below. (Not a skip:
     `prior_pass_receipt_match` — a real prior audit PASS releases automatically, so it's denied
     as a skip reason.)
   - `reason_text` — REQUIRED for `other`, `disagree_with_classification`, and
     `accept_risk_no_review` (a substantive pre-mortem), **and for the judgment codes
     (`false_positive_not_risky`, `trivial_change`, `reviewed_outside_truverifai`,
     `time_critical_hotfix`) at the write/commit gates**; a 1-sentence reason. General terms
     only — no secrets, file paths, or proprietary identifiers (same privacy rule as
     `record_outcome`).
   - `gate_context_id` — **REQUIRED. Copy it verbatim.** It is single-use and short-lived: if
     it's expired or already used, just re-run the original action so the gate issues a fresh
     one. There is **no way to skip without it** — if the gate message has no id (rare: it
     couldn't mint one), don't skip. Run `audit_coding` with `gate_repo` + `gate_diff`; a PASS
     releases the change, floor and non-floor hunks alike. You are not stuck.
   - Optionally the `gate_signal` fields (`classifier_version` / `score` / `risk_categories`) —
     forwarding them sharpens the data that tunes the classifier.

3. **Retry the original action.** The gate sees your logged skip covering it (matched via the
   server-issued gate context, bound to the hunks the gate itself recorded) and releases.

## The single-call model (you reviewed — now proceed)

Make at most **one** panel-review call per change. These two codes let you proceed on that
one review without ever calling TruVerifAI again for the same change:

- **`recommendations_applied`** — you ran ONE review (`audit_coding` / `deliberate_coding` /
  `synthesize_coding`), applied its findings, and want to proceed. The server VERIFIES a real
  review actually ran for this repo recently (it's an attestation, not a free skip). Also use it
  when a **PASS-then-modify** re-fires the gate: after a PASS, ideally write exactly what you
  reviewed — but if you tweak the content (even a comment), the gate re-fires on the changed
  bytes, and `recommendations_applied` releases it with no second review — FLOOR hunks
  included (owner ruling 2026-07-23: compliance is never penalized). The floor release is
  lineage-verified (your recent review must be on record), expires in minutes, and is logged
  distinctly as "findings applied (revision not re-reviewed)" — never as an audited PASS.
- **`review_deferred_to_commit`** — defer ALL review to the commit gate. It releases this write
  AND silences the write gate for the rest of the session/area (~1h). Use it **ONLY when you
  expect a batch of successive risky/floor writes** and want to review them together at commit
  (e.g. a multi-file migration). For a **one-off** change, don't defer — just review and proceed.
  The commit gate re-classifies the whole staged diff and still requires a real PASS for every
  floor hunk, so deferral never ships unreviewed floor code — you defer *up to* commit, never past it.

Both take the `gate_context_id` the gate printed. Never re-supply the diff or recompute a hash.

## Legitimate reasons to skip (and the matching code)

- It's a **false positive** — the flagged change isn't actually risky → `false_positive_not_risky`.
- It's a genuinely **trivial** change — cosmetic / no-op / rename-only, no risk surface → `trivial_change`.
- It was reviewed **outside** TruVerifAI (human review, another tool) → `reviewed_outside_truverifai`.
- It's **generated or vendored** code, not hand-written risk → `generated_or_vendored_code`.
- It's **test or docs** only → `test_or_docs_only`.
- It's a genuine **time-critical hotfix** and you accept the risk → `time_critical_hotfix`.
- The classifier mis-categorized it and you disagree → `disagree_with_classification` (explain).
- The review tool is **down/unavailable** → `tool_unavailable`.

### Floor classes can't be skipped with a judgment code

If the change has an **UNREVIEWED floor-class hunk — auth / secrets / money / migration /
removed-guard**, a judgment skip (`false_positive_not_risky`, `trivial_change`,
`disagree_with_classification`, `reviewed_outside_truverifai`, `time_critical_hotfix`,
`tool_unavailable`, `other`) is **denied** (`gate_skip_reason_floor_denied`).

**No skip reason releases a floor hunk. Not one — `test_or_docs_only` and
`generated_or_vendored_code` included.** They are denied too (`gate_skip_reason_floor_denied`).

The path claim can be perfectly TRUE and still not help, because it doesn't change what the hunk
**is**. The test/docs exemption has already been applied by the classifier before the gate fires —
most floor classes in a test path are demoted and never fire at all. What still floors, floors
*wherever it lives*: a real credential in a test file is a live credential someone has to rotate;
`.github/workflows/` itself classifies as test/docs, which is precisely where the CI floors matter;
and a generated/vendored path is never floor-exempt at all.

**To clear a floor hunk** — every one of these is reachable:
- `audit_coding` (a PASS) — covers floor **and** non-floor in ONE call. The recommended review.
- `confirm_floor` (**FREE**, one budget model) or `synthesize_coding` — release the FLOOR hunks, but
  only if they agree the change isn't risky.
- `accept_risk_no_review` — the logged last resort: ships it un-reviewed as an accountable override
  to the human, and needs a substantive pre-mortem.

Once the floor is covered, the **same** skip becomes admissible and clears the remaining non-floor
hunks. So on a mixed change: cover the floor first, then skip the rest.

On floor: `recommendations_applied` (with your recent review on record) releases a floor
change at **both** gates — the zero-friction compliance path. `review_deferred_to_commit`
releases a floor change at the **write gate only**; the deferred batch is re-reviewed at the
commit gate (defer *up to* commit, never past it). A **recent unrelated review does NOT release
a floor change via the recent-pass shortcut**: that valve is disabled whenever a floor hunk is
uncovered — the floor releases only through a real review, the applied attestation, a floor
tool, or the logged accept-risk override.

### A mixed change has TWO buckets, and each needs its own release

**This is the one that traps agents.** A real change usually fires BOTH kinds of hunk — some floor,
some ordinary — and they release **separately**:

| Uncovered hunk | What releases it |
|---|---|
| **FLOOR** (auth / secrets / money / migration / removed-guard) | `audit_coding` PASS · `confirm_floor` / `synthesize_coding` (low-risk verdict) · `accept_risk_no_review` |
| **NON-floor** risky | `audit_coding` PASS · `record_gate_skip` with a judgment reason (free, one line) |

**A floor tool does not release a non-floor hunk.** `confirm_floor` and `accept_risk_no_review`
cover FLOOR hunks and nothing else — so on a mixed change they will report success and the gate will
**still block**, on the ordinary hunks they never touched. Read the gate's **`Still uncovered: N
floor, M non-floor`** line and use *that bucket's* tool. An **`audit_coding` PASS covers both** in a
single call, which is usually the simplest move on a mixed change.

Once every floor hunk is covered, a judgment skip that was denied **becomes admissible** and
releases the non-floor remainder. So the order on a mixed change is: clear the floor → retry the
skip.

**Never disable the gates, and never ask the user to.** Every block has a reachable exit. If a
release tool reported success and the uncovered count did not move, you cleared the wrong bucket;
you are not deadlocked — clear the other one.

To release a floor change, **match the tool to your situation** and forward the `gate_context_id` the
gate printed (coverage then binds to the gate's own hunks, so a cosmetically drifted `gate_diff` still
releases); on a **write-gate floor block** also forward the `target_hunk_hashes = [...]` line so
coverage binds deterministically to exactly those hunks.
- **A genuine floor change you want reviewed → `audit_coding`** (same args); a PASS releases it. This
  is the **recommended** path for a real auth/secrets/money/migration/guard change. (`deliberate_coding`
  is only for a still-open design; it does not release a floor change.)
- **You believe the gate mis-fired (a false positive — an auth word in a comment, a rename, a test
  fixture) → `confirm_floor` (FREE).** Run `confirm_floor` (`gate_repo` / `gate_diff` /
  `gate_context_id`, plus `target_hunk_hashes` on a write-gate floor block). It runs ONE cheap model;
  if it agrees the change is token-shape noise it releases the gate. Material / uncertain / gate-self /
  non-floor → it releases nothing (run `audit_coding`). For a broader multi-model read, run
  `synthesize_coding` (same args; ~15–30s) — it releases only if the panel agrees it's low-risk.
- **Tool down + sustained outage →** the **commit** gate prompts a **human** to approve; the write
  gate denies. Follow the path the deny message names instead of retrying the skip. If you're
  consciously shipping it un-reviewed, `accept_risk_no_review` is the logged last resort. (Best-effort in
  automation: in a non-interactive context — `bypassPermissions` / `dontAsk` / headless `-p` —
  Claude Code auto-proceeds the prompt with no human; every prompt is logged with the raw
  `permission_mode` so the dashboard labels it honestly.)
- **Last resort — only after the real paths above genuinely don't fit** (the gate mis-fired, you're deadlocked, or you're consciously shipping un-reviewed) **→** `record_gate_skip(accept_risk_no_review,
  gate_context_id, reason_text=<pre-mortem>)`. The one judgment-style code that releases a floor
  block — but it's an accountable **override**, not a review: it ships the floor hunk un-reviewed,
  releases at **both** gates, requires a **substantive pre-mortem** (assume it IS a real issue: name
  the failure, who it affects, why it's acceptable), is bound to this one fire and expires in
  minutes, and lands a distinct override row for the human + feeds calibration. Never releases
  gate-self. Use it only when none of the real paths above fit — not to save time on a genuine change.

A reason code can also be **suspended** for a repo (calibration, off by default) if its skips keep
preceding real findings — a suspended skip is denied and you run the review.

If you **already audited this exact code**, you don't need a skip at all — the gate
releases automatically because a matching PASS receipt covers the hunks. If the gate re-fired
because you **modified the code after the review** (even a comment changed the bytes), use
`recommendations_applied` — you already reviewed, so no second review is needed. "Already
reviewed" of *unchanged* code is **not** a skip reason (it auto-releases); a prior-pass *claim*
is denied.

## When NOT to use

- The change really does touch auth/billing/migrations/secrets/load-bearing logic
  and hasn't been reviewed → **run `audit_coding` / `deliberate_coding` instead.**
- You're skipping to save time or because you feel confident → not a valid reason.
- No gate fired → there's nothing to release.

**`git commit --no-verify` won't help** — these gates are Claude Code **PreToolUse** hooks, not git
`pre-commit` hooks, so `--no-verify` never reaches them. Release the gate the real way (a review or
`record_gate_skip`), not with `--no-verify`.

## Why this matters

The skip is **logged with its reason** (free, no credits). Two things ride on it:
the user sees how often the gate is skipped and why (a high `false_positive_not_risky`
rate tells them the classifier is over-firing), and the free-form reasons train the
classifier to fire more precisely over time. An honest skip-with-reason is useful
signal; a reflexive skip to dodge review defeats the gate and pollutes that signal.
