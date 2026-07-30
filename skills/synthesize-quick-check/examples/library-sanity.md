# Example — bounded library-choice sanity check

A worked example for a library decision that's bounded (not a long-term framework commitment) but where you want quick validation.

## Scenario

You're adding date-time handling to a Next.js component. You need to format relative dates ("3 hours ago", "in 2 days") and your team doesn't have an established convention. You're between `date-fns`, `dayjs`, and `luxon` — none are wrong, but you want to know which one the community has converged on in 2026.

This is a *bounded* decision: the library is used in one feature; reversal cost is low (find/replace + reinstall); the choice doesn't commit the project long-term. Hence synthesize, not deliberate.

## How to populate the inputs

```python
mcp__truverifai__synthesize_coding(
    question=(
        "For relative-date formatting (e.g., '3 hours ago') in a Next.js 15 "
        "app in 2026, which library is most commonly used: date-fns, dayjs, "
        "or luxon? Bounded decision — used in one feature, reversible."
    ),
    context=(
        "Next.js 15 with TypeScript strict. App is bundle-size sensitive (we "
        "track payload). Currently no date library is installed. Use case is "
        "just relative formatting + a few absolute formats like 'May 25, 2026'. "
        "Tree-shaking matters."
    ),
)
```

## What a good response looks like

```json
{
  "answer": "date-fns is the most common choice in tree-shaking-sensitive Next.js projects in 2026. Bundle impact for your use case (relative formatting + a few absolute formats) is minimal because date-fns is built for tree-shaking — you import only the functions you use (e.g., `import { formatDistance, format } from 'date-fns'`). dayjs is the smaller alternative (~7kb gzipped baseline) and is reasonable if you want a single small dependency for everything, but its plugin model is less ergonomic for the locale + timezone work you might add later. luxon is the most powerful but ships ~30kb gzipped which is overkill for your stated scope. Recommended: date-fns with the specific imports `formatDistance, format` for now; revisit if you find yourself adding more locale/timezone work.",
  "answer_status": "qualified",
  "findings": [
    { "severity": "preference", "summary": "date-fns vs dayjs is partly taste — dayjs wins on baseline size if you want one tiny dependency for everything." },
    { "severity": "minor", "summary": "Recommendation is scoped to the stated use case; revisit if locale/timezone needs grow." }
  ],
  "action": "proceed_with_caveats",
  "agreement_score": 0.86,
  "models_participating": ["gpt-5.4", "claude-sonnet-4-6", "gemini-3-flash", "grok-4-1-fast"]
}
```

## How to act on this

`answer_status = qualified` → there's a clear recommendation, but the `findings` carry real caveats (the choice is partly taste; it's scoped to the current use case). Use the answer, but honor those caveats. The advisory `action = proceed_with_caveats` matches, and `agreement_score = 0.86` is auxiliary corroboration — strong but not unanimous, as expected when multiple reasonable options exist.

1. Install date-fns. Use targeted imports (`import { formatDistance } from 'date-fns'`) to keep bundle impact minimal.
2. Implement the date formatting in your component.
3. Capture the "revisit if you add locale/timezone work" caveat as a comment near the usage.

## What this example demonstrates

- "Bounded" qualifier in the question helps the synthesize call answer the right question (community standard for a one-feature library).
- Context surfaced bundle-size constraint, which directly shaped the recommendation.
- A `qualified` `answer_status` (with an agreement_score in the 0.8-0.9 range as auxiliary corroboration) is normal for library-choice questions where multiple reasonable options exist; the `findings` and response text capture the nuance.
- The answer includes a specific recommendation (date-fns with targeted imports) + a future-revisit trigger — actionable now, with a clear signal for when to reconsider.

## Variant — when this would NOT be synthesize

Same library decision can become deliberate territory if the scope changes:

- "Should we standardize on a date library for the whole monorepo for the next 3 years?" → deliberate. Long-term commit.
- "Building a date-handling library ourselves vs picking an existing one?" → deliberate. Multi-file commit, real trade-off space.

If the decision is genuinely bounded — one feature, one place, easy to swap — synthesize is right.
