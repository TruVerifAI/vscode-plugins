# `useful` vs not — boundary cases

The `useful` field is broadly defined: did the response inform your decision-making in any way? Concrete categories that count as `useful=true`:

- **Caught something** — the response surfaced an issue (security gap, performance pitfall, edge case) you hadn't considered.
- **Confirmed something** — the response validated your proposed approach. Your decision didn't change, but the confirmation has value (you proceeded with more confidence).
- **Surfaced a tradeoff** — the response identified a trade you hadn't explicitly considered (e.g., "X is simpler but Y is faster").
- **Provided context** — the response brought architectural / domain context that informed your framing of the next step.

What counts as `useful=false`:

- **Duplicated what you already knew** — the response surfaced no new information; you already had everything in the prompt.
- **Was noise** — the response was off-topic, repeated obvious things, or didn't engage with the actual decision.
- **Was wrong** — the response made a factual or reasoning error that you had to discount.

When `useful=false`, notes are required. Write 1-2 sentences on what specifically failed.

## Boundary case: `useful=true, changed_decision=false`

This is the "confirmed the existing approach" cell. Common for synthesize and deliberate calls. The response helped you proceed with more confidence even though the outcome was the same. Mark as `useful=true, changed_decision=false` — and **notes ARE required**, because `changed_decision=false` is one of the three conditions that trigger the notes requirement (the no-op cases are the most informative for the user). A short note like "confirmed the original approach; proceeded unchanged" suffices.

## Boundary case: `useful=false, changed_decision=true`

Rare but possible — you changed your mind even though the call was bad (e.g., the bad response made you realize the question itself was wrong). Notes required: explain the indirect path to the decision change.
