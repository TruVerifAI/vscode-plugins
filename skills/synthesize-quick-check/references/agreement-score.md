# Interpreting `agreement_score` for synthesize responses

**First, the framing:** `answer_status` (`settled` / `qualified` / `contested` / `unresolved`) is now the primary signal in a synthesize response — it's the synthesized verdict. `agreement_score` is **auxiliary** convergence telemetry. It does NOT drive the verdict or the `action`, and it is not something you "act on" as a decision. It remains genuinely useful as a *hint* for one specific call: whether to escalate to deliberate. Low alignment on a question you expected to be canonical is informative. So this file is still live — just read the thresholds below as an escalation heuristic layered on top of `answer_status`, not as the verdict itself.

The synthesize response carries `agreement_score` (0-1) summarizing how aligned the four panel models were on the answer. Unlike deliberate, synthesize does NOT do conflict-targeted revision — it just synthesizes the four parallel answers and reports alignment.

## Threshold guide

| Score | Interpretation | Recommended action |
|---|---|---|
| ≥0.9 | All four models converged. The answer is robust. | Use the answer. |
| 0.8-0.9 | Strong consensus with minor variation. | Use the answer; note any caveats in the response text. |
| 0.7-0.8 | Mostly aligned but with real divergence. | Use cautiously; verify against another source if the stakes are non-trivial. |
| 0.5-0.7 | Models genuinely disagreed (often paired with `answer_status` of `contested`). | **Escalate to `deliberate-before-implementing`.** The question is harder than synthesize can resolve. |
| <0.5 | Wide disagreement; synthesize couldn't converge (often paired with `answer_status` of `unresolved`). | Treat as undecided. Escalate to deliberate OR rethink the question — sometimes wide disagreement signals you asked something ambiguous. |

Read the score alongside `answer_status`: a `contested` or `unresolved` status is the primary signal that the question isn't settled; the score above is the corroborating convergence hint.

## What "synthesize agreement" actually measures

The synthesize primitive runs the same question through four models in parallel, then has the aggregator (Claude) synthesize the responses into one answer + report how aligned they were. The agreement score is the aggregator's self-reported assessment of alignment, NOT a mechanical token-similarity score.

This means:

- Two models can word their answers very differently while agreeing on substance → high agreement_score.
- Two models can use similar wording while differing on a key qualifier → lower agreement_score.

Trust the score as an alignment signal, not as a similarity signal.

## When low agreement is informative

A `contested` or `unresolved` `answer_status` — or, as the auxiliary hint, an `agreement_score < 0.7` — on a question you expected to be canonical is itself a useful signal:

- "Idiomatic way to format dates in Go" with score 0.5 means there are competing conventions in different communities. Worth knowing.
- "Standard HTTP retry pattern" with score 0.4 means the field is fragmented. Pick based on YOUR constraints, not a generic recommendation.
- "Best library for X" with score 0.3 usually means the question is dependent on context the synthesize call didn't have.

Use the low agreement as a prompt to either:

1. Re-ask with more context (`context` field) and re-run.
2. Escalate to deliberate with explicit `options_considered`.
3. Read the response text to see WHICH dimensions the models split on, then decide what to do.

## When high agreement might be deceptive

Rare, but real: four models agreeing on something doesn't mean they're right. The training data overlap across models is significant; they share blind spots.

Watch out for high-agreement answers when:

- **The question is about something recent.** Models trained before some change may all be wrong consistently. Cross-check with current docs.
- **The question is about something niche.** Low-frequency topics get cargo-culted answers from a small pool of sources.
- **The question has a community-standard answer that's wrong.** "Best practice" can become outdated; all four models inherit the same outdated wisdom.

High agreement_score is necessary but not sufficient for "this is the right answer." Use judgment; cross-check load-bearing decisions.

## The relationship to deliberate's agreement_score

The same field appears in deliberate responses, with similar semantics but in a different context:

- Synthesize: the four parallel answers and how aligned they were.
- Deliberate: the four refined answers AFTER conflict-targeted revision, and how aligned they were after the revision round.

A deliberate agreement_score of 0.85 is more meaningful than a synthesize agreement_score of 0.85 — deliberate did the work of routing conflicts back to each model. The two scores are not directly comparable; use them as signals within their own primitive's contract.
