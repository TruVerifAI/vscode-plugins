# Severity tags

The audit response carries findings tagged with one of four severity levels. The tag tells you how to respond to each finding.

## `critical`

> The change should not ship as written.

Critical findings are correctness, security, or data-integrity issues that will cause production problems. Examples:

- "The refresh-token rotation logic has a TOCTOU race: between the read of the old token and the write of the new token, a concurrent refresh request could authorize against the stale value."
- "The migration adds a NOT NULL column without a default, which will fail on the populated `users` table during deploy."
- "Input validation is missing on the `email` field — SQL injection risk if any downstream code uses it in a raw query."

**How to respond:**

1. Address the finding before committing. Do not commit a change with an unaddressed critical finding.
2. If you disagree with the finding, escalate to the user — don't unilaterally dismiss it. Explain why you disagree; let the user make the call.
3. If you address it, re-audit the revised change. Don't assume the fix is correct without verification.

## `major`

> A real problem that must be fixed before shipping.

Major findings sit between `critical` and `minor`: a genuine defect that must be fixed before the change ships, but not a ship-stopping correctness or security hole like a critical. A major finding raises the `action` to `request_changes`. Examples:

- "The rotation path doesn't handle the case where the user has two active sessions — one session will be silently logged out on the next refresh. Functionally broken for multi-device users, but not an auth bypass."
- "The migration backfills the new column in a single UPDATE over 850k rows, which will hold a lock long enough to stall login traffic during the deploy window."
- "The error response returns the raw exception string to the client, exposing internal table names — needs sanitizing before release."

**How to respond:**

1. Address the finding before the change ships — major findings are not optional follow-ups.
2. If you can't address it in this change, the change isn't ready; don't ship around it.
3. If you disagree, document why and surface it to the user rather than silently dropping it.

## `minor`

> The change can ship but should be revised.

Minor findings are real issues that aren't shipstoppers but would improve the change. Examples:

- "Error handling on the refresh path returns a generic 500 — would be more useful to return 401 so the client can distinguish auth failure from server error."
- "The migration could be CONCURRENTLY indexed to avoid taking an ACCESS EXCLUSIVE lock on the table."
- "Test coverage is good but doesn't exercise the failure path where the DB connection drops mid-rotation."

**How to respond:**

1. Address the finding if you can do so quickly (under 10 minutes).
2. If addressing it would require more scope creep, capture it as a follow-up task and ship the current change.
3. Don't ignore minor findings entirely — they tend to compound across PRs into real tech debt.

## `preference`

> A stylistic note the author can ignore.

Preferences are suggestions about how the change could be written differently without affecting correctness or safety. Examples:

- "Function name `rotate_refresh_token` is fine but `replace_refresh_token` might read more clearly."
- "The migration could be split into two PRs for easier review."
- "Consider extracting the lock-acquisition pattern into a helper."

**How to respond:**

1. Read the preference; consider whether to adopt it.
2. If the rationale resonates, adopt it. If not, ignore it.
3. Do not block a commit on a preference-level finding.

## When the response is mixed

A typical audit returns multiple findings across severities. Read the `action` enum (see `action-classes.md`) for the audit's overall recommendation, then walk through the individual findings to address them per the rules above.

If `action` is `escalate_to_human`, the audit thinks there's enough uncertainty or risk that you should not unilaterally proceed even if the critical findings appear addressable. Surface the audit output to the user and let them decide.
