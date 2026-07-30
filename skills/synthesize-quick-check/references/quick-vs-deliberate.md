# Quick check vs deliberate — when to use which

The synthesize and deliberate primitives have overlapping use cases. This file clarifies the decision boundary so the agent picks the right one.

## At a glance

| Use synthesize when… | Use deliberate when… |
|---|---|
| Question has a likely-canonical answer; you want validation | Multiple defensible answers exist |
| Reversal cost is low (single file, single function) | Reversal cost is high (multi-file, migration, breaking API) |
| ~15-30s fits the decision | a few minutes is justified by the stakes |
| You don't need conflict-targeted revision | You want models to engage with each other's specific positions |
| The question is bounded and self-contained | The question has a trade-off space worth enumerating |

## Mental model

**Synthesize** = "I want multiple smart people to glance at this and tell me if I'm on the right track. If they mostly agree, I'll go with it."

**Deliberate** = "I'm making a decision I'll live with for a while. I want multiple smart people to argue with each other about it, find the load-bearing trade-offs, and tell me where they actually disagree."

The difference is engagement depth. Synthesize is parallel-then-merge; deliberate is parallel-then-conflict-target-then-revise. Both are useful; they're for different question shapes.

## Synthesize-appropriate examples

- "What's the idiomatic way to format dates in Go for ISO 8601 output?"
- "Is there a standard pattern for retrying flaky HTTP requests in Python?"
- "Should I use `useEffect` or `useLayoutEffect` here?"
- "Is `defer` cheaper than try/finally in Go?"
- "Among `axios`, `fetch`, and `ky`, which is most commonly used in Next.js apps in 2026?"

These have likely-canonical answers; you want validation across multiple models in case one is wrong or out of date.

## Deliberate-appropriate examples

- "Should we use REST or GraphQL for our public API?"
- "What schema should we use for the events table — normalized or JSONB?"
- "How should we structure the auth flow — token-based or session-based?"
- "Which CSS framework should we commit to for the next 2 years?"
- "Should this functionality go in service A or service B?"

These have multiple defensible answers; the cost of choosing wrong is significant; you want models engaging with each other.

## When synthesize escalates to deliberate

You called `synthesize_coding` and got an `answer_status` of `contested` or `unresolved`. That's the primary signal: the question is harder than you thought. A low `agreement_score` (< 0.7) is a secondary, corroborating hint pointing the same way — but the `answer_status` is the verdict to act on.

What this means in practice:

1. Read the synthesize answer. The disagreement may be on a dimension you didn't realize mattered. Sometimes this reveals you were asking the wrong question.
2. Structure the question for deliberate. The synthesize question was probably bounded ("idiomatic pattern for X?"); the deliberate question needs to enumerate the options space ("X with approach A vs B vs C, trade-offs are...").
3. Call `deliberate-before-implementing` with the structured `options_considered` field populated.
4. Don't just re-run synthesize hoping for a cleaner `answer_status` on retry. The status (and the agreement score behind it) reflects the actual model disagreement; running again won't change it.

## When deliberate de-escalates to synthesize

Rare but real. You started preparing a deliberate call and realized the question is actually bounded — there's a canonical answer, you just didn't know it. Don't force the heavyweight primitive on a lightweight question:

- If `options_considered` is hard to populate because you can't name multiple defensible options → it's probably a synthesize question.
- If the cost of getting it wrong is "I'll change my mind in 10 minutes" → it's a synthesize question.

## Anti-pattern: using both for the same decision

Don't synthesize first, then deliberate, then audit. That's three calls for one decision. Pick the right primitive once.

The exception: synthesize → deliberate is fine when synthesize *surfaces that the question is harder than you thought* (an `answer_status` of `contested`/`unresolved`, with a low agreement_score as the corroborating hint). Don't pre-emptively call synthesize as a "warm-up" — call the right primitive directly.

## Latency reminder

| Primitive | Latency |
|---|---|
| synthesize | ~15-30s |
| deliberate | a few minutes |
| audit | a few minutes |

The latency difference is real but not load-bearing for most decisions. Pick by question shape, not by speed.
