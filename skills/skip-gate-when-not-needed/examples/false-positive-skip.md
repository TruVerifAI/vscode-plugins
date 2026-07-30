# Example — skipping a gate false positive

## The situation

You add a docstring to a test helper. The write gate flags it:

```
TruVerifAI flagged a code_review change worth a review before it ships.
What fired (1 hunk; 0 already reviewed, 1 need review):
  - code_review - matched `def` at tests/helpers/fixtures.py:12
  Still uncovered: 0 floor, 1 non-floor. NON-floor only: `audit_coding` PASS, or
  `record_gate_skip` with a judgment reason (e.g. false_positive_not_risky) — free, one line.
  Floor tools (confirm_floor / accept_risk) release NOTHING here.
This is finished code, so the natural review is `audit_coding` — run it ONCE with your
proposed_action, AND pass:
  gate_repo = "repo_268c1440e37b3de823d2ace6"
  gate_diff = the change you're about to write
  gate_context_id = "gc_5f3a9c1b2d4e6f80"
  target_hunk_hashes = ["a1b2c3d4e5f6a7b8"]
A PASS releases the gate. ...
Or, if the NON-floor hunks in 'Still uncovered' genuinely don't need review, call
`record_gate_skip` (free) with a judgment reason_code, gate_repo, and the gate context
below (copy it verbatim), then retry.
  gate_context_id = "gc_5f3a9c1b2d4e6f80"
  gate_signal = classifier_version="2.12.0" score=20 risk_categories="code_review"
```

The flagged file is `tests/helpers/fixtures.py` and the change is a docstring. This
is a genuine false positive — no risk surface, and it's confined to a test file.

## The right move

```
mcp__truverifai__record_gate_skip(
  gate_repo       = "repo_268c1440e37b3de823d2ace6",
  reason_code     = "test_or_docs_only",
  gate_context_id = "gc_5f3a9c1b2d4e6f80",
)
```

Then retry the Write — the gate sees the logged skip and releases. Pass the
`gate_context_id` the gate printed (copy it verbatim); it is **required**. The server
verifies a gate really fired and releases exactly the hunks **it** recorded, so you never
supply hunks yourself. If the gate message carries no id (rare — it couldn't issue one),
don't skip: run `audit_coding` with `gate_repo` + `gate_diff`.

## A counter-example (do NOT skip)

The gate blocks a Write to `auth/session.py` that changes how a session token is
validated. You feel confident it's correct. **Do not skip.** Confidence is not a
reason — a `Write`/`Edit` is finished code, so run `audit_coding` (its natural
review; a PASS releases). This is exactly the kind of change the gate exists for.
Auth is a **floor class**, so a judgment skip (`false_positive_not_risky`,
`disagree_with_classification`, …) is denied here anyway — if the panel agrees it's
genuinely low-risk, a `synthesize_coding` SYNTH_CONFIRM is the cheap release; if it
surfaces real risk, you're glad you checked.
