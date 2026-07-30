# Example — deliberating on API contract shape

A worked example for choosing between REST and GraphQL for a public-facing API where the decision commits the project long-term.

## Scenario

Your team is designing a public partner API. Two partners have asked for programmatic access; one is a Node.js shop, one is a Python data team. You expect 5-15 partners over the next year. The question: should the API be REST, GraphQL, or some combination?

## How to populate the inputs

```python
mcp__truverifai__deliberate_coding(
    question=(
        "Should the new partner API be REST, GraphQL, or a combination? "
        "Two partners signed; 5-15 expected within a year. Need to ship "
        "v1 in 6 weeks."
    ),
    relevant_code=(
        "# Existing internal API surface (not partner-facing):\n"
        "# Flask + REST: /api/query, /api/conversations, /api/usage, etc.\n"
        "# All JSON; all auth via Bearer tokens; standard CRUD shapes.\n\n"
        "# Expected partner use cases:\n"
        "# - Bulk-submit queries (batch ingest)\n"
        "# - Read conversation history for analytics\n"
        "# - Fetch user-level usage stats\n"
        "# - Webhook subscription management"
    ),
    architectural_context=(
        "Backend is Flask on Replit. We already have internal REST endpoints "
        "matching most of these use cases. Partners are expected to integrate "
        "into their existing toolchains (analytics dashboards, ETL pipelines). "
        "We've never run a public partner API before; this is greenfield."
    ),
    options_considered=(
        "Option A: REST with versioned paths (/api/v1/...)\n"
        "  Pros: Matches our existing internal API shape — partial code reuse "
        "of handlers. Curl-friendly. No new client library needed. Easy to mock. "
        "Partners can integrate without learning GraphQL.\n"
        "  Cons: More endpoints to design and version. Over-fetch problems for "
        "partners with diverse query needs. Multi-resource queries require "
        "multiple round trips.\n\n"
        "Option B: GraphQL with a single /graphql endpoint\n"
        "  Pros: Partners query exactly what they need. Self-documenting via "
        "introspection. Strong typing for partner SDKs via codegen.\n"
        "  Cons: Requires GraphQL client libraries in partner stacks (extra "
        "dependency). Harder to cache (no URL-based caching). Query-complexity "
        "DOS attack surface needs explicit handling. We have no GraphQL "
        "expertise on the team.\n\n"
        "Option C: REST primary + a single /analytics/graphql endpoint\n"
        "  Pros: Common cases stay REST; complex analytics queries get GraphQL "
        "ergonomics. Partners can pick either based on use case.\n"
        "  Cons: Two contracts to maintain. Partners need both clients. "
        "Conceptual complexity creep.\n\n"
        "Option D: Status quo — defer the public API, partners use internal "
        "endpoints with API keys for now\n"
        "  Pros: Zero new work. Lets us learn what partners actually want "
        "before committing to a contract.\n"
        "  Cons: Blocks the two committed partners. Internal endpoints "
        "weren't designed with partner stability in mind — we'll break them "
        "and partners will be angry."
    ),
    constraints=(
        "Ship in 6 weeks. Team has no GraphQL production experience. Two "
        "partners are already lined up — can't punt indefinitely. Must "
        "support both Node.js and Python partner stacks at minimum."
    ),
)
```

## What a good deliberation response looks like

```json
{
  "conclusion": "Recommend Option A (REST with versioned paths). The team's lack of GraphQL experience + the 6-week timeline + the partners' diverse stacks all point toward REST for V1. Document a path to add a GraphQL layer in V1.1 if partners ask for it. Option D (status quo) is rejected because committed partners need a stable contract.",
  "recommendation": "qualified",
  "findings": [
    { "severity": "minor", "summary": "Over-fetch / multi-round-trip cost on multi-resource partner queries; revisit if a GraphQL-style analytics need emerges." }
  ],
  "action": "proceed_with_caveats",
  "action_basis": "derived",
  "action_reason": "",
  "agreement_score": 0.88,
  "dimensions_of_disagreement": [
    {
      "model": "claude-sonnet-4-6",
      "severity": "low",
      "model_stance": "Suggested REST + GraphQL endpoint for analytics is worth doing if the analytics use case is significant enough; flagged that 'add it later' often becomes 'never'.",
      "consensus_stance": "Start REST-only; add GraphQL when there's concrete demand.",
      "disagreement": "Whether the analytics use case justifies the GraphQL endpoint upfront."
    }
  ]
}
```

## How to act on this

`recommendation = qualified`, `action = proceed_with_caveats` → adopt the recommended path with its caveats. One low-severity dissent on the dimensions; `agreement_score = 0.88` is auxiliary, confirming the panel converged.

1. **Adopt the consensus: REST with versioned paths.** Use `/api/v1/...` as the path prefix; design endpoints for each partner use case (batch query submit, conversation history, usage stats, webhook management).
2. **Address the dissent in your design doc.** Note that GraphQL was considered and deferred to V1.1; commit to revisit if at least 2 partners ask for it.
3. **Don't gold-plate the V1 REST design.** It's REST — pick reasonable resource shapes and ship. The deliberation already weighed the trade-offs; don't re-litigate them in the design.
4. **Document the V1.1 GraphQL addition path.** Concretely: "If 2+ partners request GraphQL-style queries over analytics endpoints, add `/api/v1/analytics/graphql` with the same auth model. Don't ship GraphQL on the CRUD endpoints — REST is sufficient there."
5. **Communicate the decision to partners** in the v1 announcement. "REST API at /api/v1/... — we considered GraphQL and may add it for analytics queries in v1.1 based on partner feedback."

This is a classic deliberation outcome: clean consensus, one low-severity dissent worth capturing as a future-iteration trigger, no need to surface to the user since the action is `proceed_with_caveats` and the dissent is low-severity.
