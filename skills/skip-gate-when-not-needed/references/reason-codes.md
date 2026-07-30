# `record_gate_skip` reason codes

Every call takes `gate_repo` and the **`gate_context_id`** the gate printed (required — the server
verifies a gate fired and releases exactly the hunks it recorded).

Pick the closest single fit. `reason_text` is required for `other`,
`disagree_with_classification`, and `accept_risk_no_review` (a substantive pre-mortem) — **and for
the judgment codes (`false_positive_not_risky`, `trivial_change`, `reviewed_outside_truverifai`,
`time_critical_hotfix`) at the write and commit gates.** It's welcome on any code.

Past 500 characters `reason_text` is clipped — 4,000 for `accept_risk_no_review`, whose pre-mortem is
the accountability record. You're told when it happens and the call still succeeds, so never retry
just to shorten it. All skips are logged.

| reason_code | Use when | reason_text |
|---|---|---|
| `false_positive_not_risky` | The gate flagged it, but the change genuinely isn't risky (the classifier over-fired). | required at write/commit |
| `trivial_change` | A cosmetic / no-op / rename-only change with no risk surface. | required at write/commit |
| `reviewed_outside_truverifai` | A human or another tool already reviewed it. | required at write/commit |
| `generated_or_vendored_code` | Generated / vendored / lockfile content, not hand-written risk. | optional |
| `test_or_docs_only` | The change is confined to tests or documentation. | optional |
| `time_critical_hotfix` | A genuine production hotfix where you accept the unreviewed risk. | required at write/commit |
| `disagree_with_classification` | The classifier mis-categorized the change and you disagree with the flag. | **required** |
| `tool_unavailable` | The review tool errored / is down and you can't run it. | optional |
| `other` | None of the above. | **required** |
| `recommendations_applied` | You ran ONE review, applied its findings (or a PASS-then-modify re-fired the gate) and want to proceed. Server-verified against a real review. Releases FLOOR hunks too (lineage-verified, minutes-TTL, logged distinctly as "findings applied"). | optional |
| `branch_already_reviewed` | MERGE commits only (server-enforced): the branch content was already reviewed per-commit, but the merge diff's re-hunked boundaries match no receipt. Releases the non-floor hunks; NEW/conflict floor content still needs a real PASS. | **required** — name where it was reviewed (PRs / commits) |
| `review_deferred_to_commit` | Defer a batch of successive risky writes to the commit gate; releases this write + silences the write gate for the session (~1h). | optional |
| `accept_risk_no_review` | **LAST RESORT on a FLOOR block only** — you've decided to ship the floor change un-reviewed. Logged as an accountable override to the human, not a review; bound to this one fire, expires in minutes. | **required** — a substantive pre-mortem |

## Floor classes — while a floor hunk is UNREVIEWED, a judgment skip is **denied**

While a change has an **unreviewed floor-class hunk — auth / secrets / money / migration /
removed-guard**, the judgment and external-trust codes (`false_positive_not_risky`,
`trivial_change`, `disagree_with_classification`, `reviewed_outside_truverifai`,
`time_critical_hotfix`, `tool_unavailable`, `other`) are **denied** — those classes "need a real
check, not a judgment call." Only the **path-verified** codes (`test_or_docs_only`,
`generated_or_vendored_code`) can release a floor change on that basis, and only when the server
confirms the path class from fire-time evidence.

**A mixed change has two buckets.** Most real changes fire both floor and ordinary risky hunks, and
they clear **separately**: floor hunks on an `audit_coding` PASS / `confirm_floor` /
`synthesize_coding` / `accept_risk_no_review`; ordinary hunks on an `audit_coding` PASS or a
judgment code here. A floor tool releases floor hunks **only**, so on a mixed change it can succeed
while the gate still blocks on the ordinary hunks it never touched. Read the gate's
`Still uncovered: N floor, M non-floor` line and use that bucket's tool — an `audit_coding` PASS
covers both in one call. **Once every floor hunk is covered, a judgment code here becomes
admissible** and releases the non-floor remainder.

The two **single-call** codes differ on floor (rev-3, 2026-07-23): **`recommendations_applied`**
releases a floor change at **both** gates — lineage-verified against your recent real review,
minutes-TTL, logged distinctly as "findings applied" (compliance is never penalized).
**`review_deferred_to_commit`** is **write-gate-only**: it defers the batch, and the commit gate
re-audits every floor hunk on the real staged bytes — defer *up to* commit, never past it.

### `accept_risk_no_review` — the last-rung floor override

`accept_risk_no_review` is the **only judgment-style code that releases a floor block**, and it is
**not a review** — it's an accountable, logged *override*: you are shipping the floor change
un-reviewed and accepting responsibility. It is a **genuine last resort** — reach for it only after
the real paths (`audit_coding`, or `confirm_floor` / `synthesize_coding` for a false positive)
genuinely don't fit: the gate mis-fired, you're deadlocked, or you're consciously choosing to ship
un-reviewed. It:

- releases the floor at **both** the write and commit gate (unlike apply/defer, which are write-only),
- requires a **substantive pre-mortem** `reason_text` (assume it IS a real issue: name the failure,
  who it affects, why shipping it un-reviewed is acceptable — a one-word "fine" is rejected),
- is **bound to that one gate fire's floor hunks** and expires in **minutes** (a later edit re-fires
  the gate and needs a fresh accept-risk),
- lands a **distinct override row** on the admin dashboard (repo, hunks, your pre-mortem, timestamp)
  and feeds the skip→incident calibration loop,
- can **never** release a **gate-self** change (the gate's own files always need a real audit).

To release a floor change *without* accepting un-reviewed risk, **match the tool to your situation**
(the gate's deny message spells this out). **It works the same at the commit gate and the write gate.**
Always pass the `gate_context_id` the gate printed (it binds coverage to the gate's own hunks). On a
**write-gate** block, also copy the `target_hunk_hashes = [...]` line **whole and verbatim** — it lists
every risky hunk of the change, and a partial list narrows what your review covers:

- **A genuine floor change you want reviewed →** run `audit_coding` with your `proposed_action` +
  `gate_repo`/`gate_diff`/`gate_context_id`; a PASS releases it. This is the **recommended** path for a
  real auth/secrets/money/migration/guard change. (`deliberate_coding` is only for a still-open design;
  it does **not** release a floor change.)
- **You believe the gate mis-fired (a false positive — an auth word in a comment, a rename, a test
  fixture) →** run `confirm_floor` (free, one model) with `gate_repo` + `gate_diff` + `gate_context_id`,
  or `synthesize_coding` (same args; ~15–30s) for a broader multi-model read. Each releases the
  **floor hunks**, and only if it agrees the change isn't actually risky; if it finds the change
  material, run `audit_coding`. Neither touches a NON-floor hunk — if the gate's `Still uncovered`
  line still lists any, release those with a judgment code here or an `audit_coding` PASS.
- **Review tool down + sustained outage →** the **commit** gate prompts a **human** to approve
   (`permissionDecision: "ask"`); the write gate simply denies. You cannot approve your own prompt.
   If you're consciously shipping the change un-reviewed, `accept_risk_no_review` is the logged
   last resort — it releases the floor hunks at either gate. (This human gate is **best-effort in automation**: in a non-interactive context
   — `bypassPermissions` / `dontAsk` / headless `-p` — Claude Code auto-proceeds the prompt with no
   human present. Every prompt is logged with the raw `permission_mode` so the dashboard labels it
   honestly rather than claiming a human always decided.)

## A reason code can be **suspended** (Phase 5 calibration)

If a reason code's skips keep preceding real findings, the maintainers' calibration loop can
**suspend** that code for that repo. A suspended code's skip is denied
(`gate_skip_reason_code_suspended`) and you run the real review instead — re-run `audit_coding`
with `gate_repo` + `gate_diff`. `tool_unavailable` is never suspendable (it's the outage valve).
This is **off by default** and only enabled by a maintainer on real usage data, so you'll rarely
see it; when you do, it's not an error to report — just run the review.

## `time_critical_hotfix` records a deferred-review obligation

A `time_critical_hotfix` skip proceeds immediately but logs an **open obligation** to
review the change later. A subsequent `record_gate_skip` in the same repo may surface a
non-blocking `advisory` reminding you the hotfix still needs a real review; it resolves once a
later `audit_coding` covers the same hunks. The skip isn't blocked — this is a reminder, not a gate.

## The single-call codes — `recommendations_applied` / `review_deferred_to_commit`

Make at most **one** panel-review call per change; these two proceed on that one review:

- **`recommendations_applied`** is server-**verified** — it's accepted only if a real review
  receipt (`audit` / `deliberate` / `synthesize`, any verdict) exists for this repo recently.
  It's not a free skip; it attests you ran the review and addressed it. Use it after applying a
  review's findings, or when a **PASS-then-modify** re-fired the gate (you changed the reviewed
  bytes — even a comment — so the gate re-classifies; no second review is needed). A floor hunk
  released this way at the **write** gate is still re-audited at commit; at the **commit** gate
  the applied call is the final release (rev-3).
- **`review_deferred_to_commit`** needs no prior review — it's an explicit "review later." It
  releases this write and silences the write gate for the session/area (~1h), and logs an **open
  obligation** (a later `record_gate_skip` may surface a non-blocking advisory). Use it **only when
  you expect a batch of successive risky/floor writes** to review together at commit; for a one-off
  change, just review and proceed. The commit gate re-classifies the whole staged diff and requires
  a real PASS for every floor hunk — deferral never ships unreviewed floor code.

Both take the `gate_context_id` the gate printed; never re-supply the diff or recompute a hash.

### `prior_pass_receipt_match` is **not** a skip (don't use it to skip)

`prior_pass_receipt_match` is **not a way to skip**: if you genuinely already passed an
`audit_coding` of this *exact* code, the gate releases **automatically** — a matching PASS
receipt covers the hunks, so no skip is needed. If the gate still fired, the code **changed**
since that review, so re-run the review (you can scope `audit_coding` to just the
changed/uncovered hunks — the prior PASS still covers the rest). Recording a skip with this
reason is **denied at every gate**.

## Honesty matters

`false_positive_not_risky` and `disagree_with_classification` are the codes the
maintainers watch most — a high rate signals the classifier needs tuning, and the
free-form text is the training signal. `time_critical_hotfix` and
`disagree_with_classification` are the codes most open to lazy use; reserve them for
when they're true. When unsure whether a skip is justified, run the review instead.

## Privacy

Same rule as `record_outcome`: `reason_text` must not contain secrets, proprietary
file paths, function/class names, or copied source. Describe the change in general
terms ("removed an unused import in a test helper"), not specifics.
