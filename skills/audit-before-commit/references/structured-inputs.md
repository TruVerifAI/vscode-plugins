# Structured inputs for `audit_coding`

The audit's output quality is gated on the input quality. Agent-prepared structured inputs produce decision-grade findings; raw diff dumps produce generic ones. Populate each field per the rules below.

## `proposed_action` (required)

A 2-4 sentence summary of WHAT changes and WHY. This is the audit's framing — the four models read this first to understand the agent's intent before looking at the code.

**Good:**

> "Adding refresh-token rotation to the OAuth flow. On each token refresh, the server now invalidates the old refresh token and issues a new one. Goal: limit replay-attack window if a refresh token is exfiltrated."

**Bad (dump of the raw diff — the audit can't tell intent from this):**

> "diff --git a/auth.py b/auth.py\n@@ -45,7 +50,12 @@\n+def rotate_refresh_token(...)..."

**Bad (vague):**

> "Auth changes"

The proposed_action tells the audit what success looks like. If the audit can articulate the change back to you correctly, it can also tell you what's wrong with it.

## `relevant_code` (highly recommended)

The actual code being changed — the diff, the before/after, or the changed file in full if small. Scope this to the *change surface plus enough surrounding context for a reviewer to assess the change*.

**Rule of thumb:** include the function or class being modified, plus any direct callers or callees that interact with the changed surface. Don't dump entire files when only one function changed; don't truncate to just the changed lines when the change's correctness depends on surrounding code.

**Good example for the OAuth case above:**

- The full `rotate_refresh_token` function (new)
- The `refresh_access_token` function it's called from (modified)
- The DB schema for `refresh_tokens` table
- Any test that exercises the refresh path

**Bad:**

- Just the diff hunks (the audit can't see what calls what)
- The entire `auth.py` (too much; audit attention dilutes)

## `tests` (recommended; include `none` explicitly if there aren't any)

Existing tests covering the affected code, plus any new tests added alongside the change. Include the test names + their setup + the assertions. If there are NO tests covering this surface, say so explicitly:

> "No existing tests cover the refresh-token rotation path. No new tests added."

An explicit "no tests" is more useful than omitting the field — it tells the audit to flag the test gap as a finding.

## `architectural_context` (recommended)

Related systems, design decisions, ADRs, or system-level constraints that bear on this change. The audit uses this to understand whether the change is consistent with intent at a higher level than the code.

**Helpful contents:**

- Which user-facing flows this affects ("Used in the login + the API key refresh paths")
- Related ADRs ("Per ADR-12 we use short-lived access tokens with long-lived refresh tokens")
- Cross-system dependencies ("The Stripe webhook handler also reads refresh tokens — we'd need to verify this change doesn't break it")
- Recent related changes ("We added 2FA last sprint; this is the refresh-token-rotation half of that work")

If you don't know what's relevant, name the high-level system the change belongs to:

> "Auth layer. Tokens issued during login and refreshed via /api/auth/refresh."

That's enough context for the audit to ask the right questions even if you don't enumerate every dependency.

## `constraints` (recommended)

Hard requirements the audit should respect when judging the change. Without these, the audit might flag a finding that's actually intentional given a constraint you didn't surface.

**Helpful contents:**

- Performance bounds ("p99 latency must stay under 200ms")
- Security policies ("All token storage must be encrypted at rest per our SOC 2 commitment")
- Deployment windows ("Deploying Friday; no rollback capability over the weekend")
- Regulatory considerations ("GDPR-scoped users; PII handling matters")
- API stability commitments ("Public API is versioned; we can't break v1 callers")

## Common mistakes

1. **Dumping raw diff into `proposed_action`** — kills the audit's ability to understand intent.
2. **Omitting `tests`** — the audit assumes you tested, which is often wrong.
3. **Vague constraints** ("performance matters") — give numbers and policies.
4. **No `architectural_context`** — the audit reviews the code in isolation and misses cross-system concerns.
5. **Including entire files in `relevant_code`** — dilutes audit attention; scope tighter.

When in doubt: err toward more specific framing in `proposed_action` and tighter scoping in `relevant_code`.
