# Populating `options_considered`

The `options_considered` field is the most undervalued input to `deliberate_coding`. Without it, the four models reason about a decision space they have to reconstruct from `question` + `architectural_context`. With it, they reason about the *actual* trade-off space you face.

## What goes in

A structured enumeration of the approaches you've considered, each with:

- **The approach itself** (one-sentence name + brief description).
- **The trade-offs** along the dimensions that matter for your project.
- **The status quo** if applicable ("do nothing / keep current behavior") — this is often the most important option to articulate.

## Good vs. bad framing

### Bad (vague):

```
options_considered: "I'm considering REST or GraphQL."
```

The models can't reason about your trade-offs from this. They'll fall back to generic REST-vs-GraphQL comparisons.

### Good (structured + project-specific):

```
options_considered: |
  Option A: REST with versioned paths (/api/v1/...)
    - Pros: simple, matches existing TruVerifAI surface area, easy for partners
      to mock, no GraphQL client library overhead in SDKs
    - Cons: more endpoints to design and version; over-fetch / under-fetch
      problems for clients with diverse query needs

  Option B: GraphQL with a single /graphql endpoint
    - Pros: clients query exactly what they need; schema is self-documenting;
      strong typing for partner SDKs via codegen
    - Cons: harder to cache (no URL-based caching); requires GraphQL client
      libraries in every SDK; learning curve for partners; query-complexity
      DOS attack surface

  Option C: REST + a single graph endpoint for analytics-style queries
    - Pros: most common case stays simple (REST); complex multi-resource
      queries get GraphQL ergonomics
    - Cons: two contracts to maintain; partners need both clients;
      complexity creep

  Option D: Status quo — internal API only, no public partner API yet
    - Pros: no decision needed; deferral lets us see what partners actually want
    - Cons: blocks the partner-integration roadmap; competitive risk if
      a major partner asks for an API and we punt
```

That's enough structure for the deliberation to engage with the specific trade-offs your project faces.

## Common mistakes

### 1. Missing the status quo

"Do nothing" is almost always a legitimate option. Including it forces the models to ask: is this decision urgent enough to justify the work? Often the answer reveals that the right call is to defer.

### 2. Half-articulated options

Don't enumerate options without their trade-offs. An option name alone is just a label; the trade-offs are where the deliberation engages.

### 3. Suppressing options you've already discarded

If you've already discarded an option, include it briefly with the reason you discarded it. The models may surface that your reason for discarding is wrong, or that the discarded option deserves another look.

### 4. Loading the comparison

Don't write options as if one is obviously right ("Option A: clean and pragmatic. Option B: complicated and slow."). Trade-offs are real on both sides — articulate them honestly, then let the deliberation weigh them.

### 5. Mixing options across decision levels

If you're deciding between Postgres and MySQL, that's one question. If you're deciding the table shape, that's a different question. Don't blend them — call deliberate twice, once for each level of decision.

## Length guidance

- 3-5 options is typical. More than 5 dilutes attention; fewer than 2 means you're not really deliberating.
- Each option: 30-100 words. Enough to articulate the trade-offs; not enough to bury the decision in detail.
- Total `options_considered`: 200-600 words. If it's longer than that, the decision is probably better split into two smaller deliberations.

## What this maps to in the response

The deliberation reads `options_considered`, has each model reason against the trade-offs you've surfaced, then identifies which dimensions the models disagreed on. The `dimensions_of_disagreement` response field is a function of how well-articulated your `options_considered` was. Sparse options → sparse disagreement detection → less useful output.
