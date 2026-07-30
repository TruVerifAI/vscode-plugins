# `category` picker guide

The 12-value enum, with a short trigger phrase per category. Pick the SINGLE closest fit. Use `other` only when nothing else applies (and explain in notes).

| Category | Pick when the decision was about... |
|---|---|
| `security` | auth flow, authorization checks, cryptography, input parsing/validation, PII handling, secrets management |
| `billing_credits` | payment flow, money handling, credit accounting, subscription state, refund logic |
| `data_modeling` | schema design, table/column structure, migrations, indexes, foreign keys, field nullability |
| `api_contract` | endpoint shape, request/response schema, public API versioning, RPC interface, field naming for external consumers |
| `architecture` | module/service ownership, boundary placement, dependency direction, where code lives |
| `performance` | caching strategy, concurrency model, query optimization, hot-path tuning |
| `dependency` | library / framework / SDK selection, version pin policy, drop-in vs breaking changes |
| `refactor` | restructuring load-bearing code, renaming widely-used symbols, file reorganization |
| `error_handling` | failure modes, retry/timeout strategy, fallback behavior, observability hooks |
| `testing` | test strategy, coverage approach, mock-vs-real boundaries, fixture design |
| `deployment_ops` | CI/CD pipeline, environment config, infra-as-code, rollout strategy, feature flag plumbing |
| `other` | nothing in the list fits — REQUIRED to explain in notes |

## Decision overlap

Several decisions span multiple categories. When in doubt, pick the SHAPE of the decision over the SUBJECT:

- "Adding rate limiting to the auth endpoint" → `security` if the decision is *whether to add rate limiting* (security control), `performance` if the decision is *how to size the rate limit* (tuning).
- "Migrating Stripe webhook handler to a new endpoint" → `billing_credits` if the decision is about the *flow / contract / safety*, `architecture` if it's about *where the handler lives*.
- "Choosing a logging library" → `dependency` (library selection), not `observability` (which isn't a category).

If you genuinely can't decide between two equally-good categories, pick `other` and explain in notes. That's the escape hatch — the user can re-categorize on their dashboard.
